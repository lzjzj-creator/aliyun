"""#71 离线抽取评测 harness（G3：精确率 ≥95% 的离线证据）。

口径说明（写在报告里，也是这版 harness 的诚实边界）：
- 离线环境没有真模型，LLM 由 stub 代替：它按 gold 标注产出"理想候选"，
  再注入对抗性坏候选（编造 quote、越界槽位、空 quote、非法 kind、
  越界 confidence）。
- 因此本 harness 度量的是【抽取 pipeline 的验收层】——逐字校验、槽位
  白名单、敏感词、去重——保证：存库的事实恰好等于合法事实。
    precision = 存库且命中 gold 的事实数 / 存库事实总数     目标 ≥0.95
    coverage  = 存库且命中 gold 的事实数 / gold 事实总数     目标 ≥0.95
- 真模型口径用 `--live`：同一 gold 集 + 真 Supabase + 真模型，评分含
  严格命中（kind+slot+value 完全一致）与近似命中（值互为子串）。
  需要 SUPABASE_URL / SUPABASE_SERVICE_KEY / 模型 key；结束自动清理
  throwaway owner 与种子会话。

用法：
    python tests/eval/run_extraction_eval.py          # 离线（退出码 0/1）
    python tests/eval/run_extraction_eval.py --live   # 真模型复核（#91）
"""
import copy
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

memory = importlib.import_module("backend.memory")
memory_v3 = importlib.import_module("backend.memory_v3")
security_v3 = importlib.import_module("backend.session_security_v3")
fact_extractor = importlib.import_module("backend.fact_extractor")

from tests.test_memory_v3 import Store  # noqa: E402

ANON = "anon-eval"
PRECISION_TARGET = 0.95
COVERAGE_TARGET = 0.95

# 每会话注入的对抗性坏候选：全部必须被验收层拦下，一条漏网 precision 就掉。
def _adversarial(msg_id):
    return [
        {"kind": "skill", "slot_path": "skills.groups.9.items.0",
         "value": "Fortran", "msg_id": msg_id,
         "quote": "完全没说过的话", "confidence": 0.9},          # 编造 quote
        {"kind": "skill", "slot_path": "hacked.path.0",
         "value": "x", "msg_id": msg_id,
         "quote": "x", "confidence": 0.9},                      # 槽位越界
        {"kind": "skill", "slot_path": "skills.groups.0.items.0",
         "value": "", "msg_id": msg_id,
         "quote": "", "confidence": 0.9},                       # 空值空 quote
        {"kind": "politics", "slot_path": "basics.name",
         "value": "x", "msg_id": msg_id,
         "quote": "x", "confidence": 0.9},                      # 非法 kind
        {"kind": "skill", "slot_path": "skills.groups.0.items.0",
         "value": "y", "msg_id": 999,
         "quote": "y", "confidence": 0.9},                      # msg_id 不存在
        {"kind": "skill", "slot_path": "skills.groups.0.items.0",
         "value": "z", "msg_id": msg_id,
         "quote": "z", "confidence": 1.7},                      # confidence 越界
    ]


def run():
    data = json.loads((ROOT / "tests" / "eval" / "v3_eval_sessions.json")
                      .read_text(encoding="utf-8"))
    store = Store()
    client = httpx.Client(
        base_url="https://supabase.invalid/rest/v1",
        headers={"apikey": "k", "Authorization": "Bearer k"},
        transport=httpx.MockTransport(store))
    try:
        with patch.object(memory, "_client", client):
            owner = security_v3.issue_owner(ANON)["owner"]
            oid = owner["id"]
            totals = {"stored": 0, "valid": 0, "gold": 0, "sessions": 0,
                      "rejected_candidates": 0}
            per_session = []
            for s in data["sessions"]:
                sid = s["id"]
                store.rows.setdefault("chat_sessions", []).append({
                    "id": sid, "anon_id": ANON, "title": sid[-4:],
                    "stage": "done", "created_at": "t", "updated_at": "t",
                    "last_message_at": "t"})
                for m in s["messages"]:
                    store.rows.setdefault("chat_messages", []).append(
                        {"session_id": sid, "role": "user",
                         "content": m["content"], "created_at": "t",
                         "id": m["id"]})
                gold = s.get("gold") or []
                reply = {"facts": copy.deepcopy(gold)
                         + _adversarial(s["messages"][0]["id"]),
                         "conflicts": [], "denied": []}
                before = len(store.rows["v3_facts"])
                with patch.object(fact_extractor.llm, "chat_json",
                                  lambda *a, **k: copy.deepcopy(reply)):
                    fact_extractor.extract_incremental(oid, sid)
                new_facts = store.rows["v3_facts"][before:]
                gold_keys = {(g["kind"], g["slot_path"], g["value"])
                             for g in gold}
                valid = sum(1 for f in new_facts
                            if (f["kind"], f["slot_path"], f["value"])
                            in gold_keys)
                totals["stored"] += len(new_facts)
                totals["valid"] += valid
                totals["gold"] += len(gold)
                totals["sessions"] += 1
                per_session.append(
                    {"session": sid[-4:], "gold": len(gold),
                     "stored": len(new_facts), "valid": valid})
            precision = (totals["valid"] / totals["stored"]
                         if totals["stored"] else 0)
            coverage = (totals["valid"] / totals["gold"]
                        if totals["gold"] else 0)
            print("== v3 抽取评测（验收层口径，LLM stub）==")
            for r in per_session:
                flag = "OK " if r["stored"] == r["valid"] == r["gold"] else "!! "
                print(f"{flag}{r['session']}: gold={r['gold']} "
                      f"stored={r['stored']} valid={r['valid']}")
            print(f"precision={precision:.3f} (target {PRECISION_TARGET}) "
                  f"coverage={coverage:.3f} (target {COVERAGE_TARGET}) "
                  f"sessions={totals['sessions']}")
            ok = (precision >= PRECISION_TARGET
                  and coverage >= COVERAGE_TARGET)
            print("RESULT:", "PASS" if ok else "FAIL")
            return ok
    finally:
        client.close()


def _norm_val(v):
    return "".join(str(v or "").split())


def _live_seed(store_client_unused, sessions):
    """Seed eval sessions/messages into the real DB (service role)."""
    for s in sessions:
        memory._request("POST", "/chat_sessions", json={
            "id": s["id"], "anon_id": "eval-live",
            "session_token_hash":
                hashlib.sha256(b"eval-live").hexdigest()})
        for m in s["messages"]:
            memory._request("POST", "/chat_messages", json={
                "session_id": s["id"], "role": "user",
                "status": "done", "content": m["content"]})


def _live_cleanup(owner_id, session_ids):
    for sid in session_ids:
        try:
            memory._request("DELETE", "/chat_sessions",
                            params={"id": f"eq.{sid}"})
        except Exception:
            pass
    try:
        memory_v3.delete_owner(owner_id)
    except Exception:
        pass


def run_live():
    """#91: same gold set against the real model + real Supabase.

    A stored fact 'hits' gold strictly on (kind, slot_path, value) triple
    equality; approximately when kind+slot match and values contain each
    other. Extra stored facts count against precision — the model should
    not over-extract. Report is advisory (exit 0); the human judges.
    """
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
               if not os.getenv(k)]
    if not (os.getenv("DASHSCOPE_API_KEY") or os.getenv("DEEPSEEK_API_KEY")):
        missing.append("DASHSCOPE_API_KEY or DEEPSEEK_API_KEY")
    if missing:
        print("live mode needs env:", ", ".join(missing))
        return False
    data = json.loads((ROOT / "tests" / "eval" / "v3_eval_sessions.json")
                      .read_text(encoding="utf-8"))
    try:
        owner = security_v3.issue_owner("eval-live")["owner"]
    except Exception as exc:
        print("live mode: cannot reach Supabase:", exc)
        return False
    oid = owner["id"]
    sids = [s["id"] for s in data["sessions"]]
    totals = {"stored": 0, "strict": 0, "fuzzy": 0, "gold": 0}
    per_session = []
    try:
        _live_seed(None, data["sessions"])
        for s in data["sessions"]:
            gold = s.get("gold") or []
            try:
                fact_extractor.extract_incremental(oid, s["id"])
            except Exception as exc:
                per_session.append({"session": s["id"][-4:],
                                    "error": str(exc)[:80]})
                continue
            stored = memory_v3.list_facts(oid, limit=200)["facts"]
            # restrict to this session's facts via evidence join is
            # server-side; approximate by counting new facts per session
            strict_keys = {(g["kind"], g["slot_path"],
                            _norm_val(g["value"])) for g in gold}
            gold_by_slot = {}
            for g in gold:
                gold_by_slot.setdefault((g["kind"], g["slot_path"]),
                                        []).append(_norm_val(g["value"]))
            strict = fuzzy = 0
            for f in stored:
                key = (f["kind"], f["slot_path"], _norm_val(f["value"]))
                if key in strict_keys:
                    strict += 1
                    continue
                cands = gold_by_slot.get((f["kind"], f["slot_path"]), [])
                if any(_norm_val(f["value"]) in c or c in _norm_val(f["value"])
                       for c in cands if c):
                    fuzzy += 1
            # NOTE: list_facts returns owner-wide; sessions are sequential
            # so cumulative counting is approximate — printed per-session
            # rows are cumulative; totals use the final full scan.
            totals["stored"] = len(stored)
            totals["gold"] += len(gold)
            per_session.append({"session": s["id"][-4:],
                                "gold": len(gold), "stored": len(stored)})
        # final pass: strict+fuzzy over the whole owner state
        stored = memory_v3.list_facts(oid, limit=500)["facts"]
        all_gold = [g for s in data["sessions"] for g in (s.get("gold") or [])]
        strict_keys = {(g["kind"], g["slot_path"], _norm_val(g["value"]))
                       for g in all_gold}
        gold_by_slot = {}
        for g in all_gold:
            gold_by_slot.setdefault((g["kind"], g["slot_path"]),
                                    []).append(_norm_val(g["value"]))
        for f in stored:
            key = (f["kind"], f["slot_path"], _norm_val(f["value"]))
            if key in strict_keys:
                totals["strict"] += 1
            elif any(_norm_val(f["value"]) in c or c in _norm_val(f["value"])
                     for c in gold_by_slot.get((f["kind"], f["slot_path"]), [])
                     if c):
                totals["fuzzy"] += 1
    finally:
        _live_cleanup(oid, sids)
    hits = totals["strict"] + totals["fuzzy"]
    precision = hits / totals["stored"] if totals["stored"] else 0
    coverage = hits / totals["gold"] if totals["gold"] else 0
    print("== v3 抽取评测（真模型口径，--live）==")
    for r in per_session:
        print("  ", r)
    print(f"stored={totals['stored']} strict={totals['strict']} "
          f"fuzzy={totals['fuzzy']} gold={totals['gold']}")
    print(f"precision(fuzzy hits/stored)={precision:.3f} "
          f"coverage(hits/gold)={coverage:.3f}")
    print("RESULT: REVIEW (advisory — human judges precision vs 95%)")
    return True


if __name__ == "__main__":
    if "--live" in sys.argv:
        sys.exit(0 if run_live() else 1)
    sys.exit(0 if run() else 1)

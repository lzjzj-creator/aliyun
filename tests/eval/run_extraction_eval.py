"""#71 离线抽取评测 harness（G3：精确率 ≥95% 的离线证据）。

口径说明（写在报告里，也是这版 harness 的诚实边界）：
- 离线环境没有真模型，LLM 由 stub 代替：它按 gold 标注产出"理想候选"，
  再注入对抗性坏候选（编造 quote、越界槽位、空 quote、非法 kind、
  越界 confidence）。
- 因此本 harness 度量的是【抽取 pipeline 的验收层】——逐字校验、槽位
  白名单、敏感词、去重——保证：存库的事实恰好等于合法事实。
    precision = 存库且命中 gold 的事实数 / 存库事实总数     目标 ≥0.95
    coverage  = 存库且命中 gold 的事实数 / gold 事实总数     目标 ≥0.95
- 真模型提示词层面的精确率需 #74 真库环境配合人工复核，不在本脚本范围。

用法：python tests/eval/run_extraction_eval.py   （退出码 0/1）
"""
import copy
import importlib
import json
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


if __name__ == "__main__":
    sys.exit(0 if run() else 1)

"""#23 访谈模拟评测：5 个固定 persona 走真实 turn_events 管线。

- LLM 三角色全部替换为确定性替身（编排=rule_decide / 生成=固定 token / 抽取=按脚本声明+原文 quote）
- 存储走 memory 真实 CAS 路径（httpx.MockTransport 内存库）
- 断言：事件配对与序、覆盖率单调、0 编造（更新值必须能在用户原话中找到）、
  ≤12 轮收尾（成功 persona 收敛 / 跑偏 persona 到顶收尾）、stage 终态 done
- 鉴权/限流/幂等/终端 done 由 tests/test_coach.py 覆盖，本脚本不重复

运行：python tests/coach_sim.py   → 控制台摘要 + 重写 docs/评测报告-v2.md
"""
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
}):
    from backend import coach, memory
from tests.test_memory import Store

import httpx

# ---------------- persona 剧本 ----------------
# 每轮 {user: 用户原话, slots: {slot:value}, deny: [slot], exp: [经历], skills: [组]}
PERSONAS = {
    "大一·零经历": [
        {"user": "我是大一新生，在杭电读计算机",
         "slots": {"identity.grade": "大一", "identity.school": "杭电", "identity.major": "计算机"}},
        {"user": "想做后端开发方向的岗位",
         "slots": {"basics.target_role": "后端开发"}},
        {"user": "实习肯定没有，项目也还没做过",
         "deny": ["internships", "projects"]},
        {"user": "会一点 Python 基础",
         "skills": [{"group": "语言", "items": ["Python"]}]},
    ],
    "大二·有项目": [
        {"user": "我大二，在浙大读软件工程",
         "slots": {"identity.grade": "大二", "identity.school": "浙大", "identity.major": "软件工程"}},
        {"user": "想找后端开发实习",
         "slots": {"basics.target_role": "后端开发实习"}},
        {"user": "没有实习经历",
         "deny": ["internships"]},
        {"user": "做过二手书小程序，我负责后端，用 Java 和 MySQL，上线后日活涨了30%",
         "exp": [{"type": "project", "title": "二手书小程序", "role": "后端",
                  "period": "2025.03-06", "situation": "校内二手交易需求",
                  "actions": ["搭建后端", "接入支付"], "results": ["日活上涨"],
                  "metrics": ["30%"]}],
         "skills": [{"group": "后端", "items": ["Java", "MySQL"]}]},
    ],
    "大三·有实习": [
        {"user": "我大三，东南大学计算机",
         "slots": {"identity.grade": "大三", "identity.school": "东南大学", "identity.major": "计算机"}},
        {"user": "目标是后端开发岗",
         "slots": {"basics.target_role": "后端开发"}},
        {"user": "暑假在某厂实习过两个月，做订单系统，QPS 从800提到1200",
         "exp": [{"type": "internship", "title": "某厂后端实习", "role": "后端实习生",
                  "period": "2025.07-09", "situation": "订单系统性能瓶颈",
                  "actions": ["慢查询治理", "加缓存"], "results": ["QPS提升"],
                  "metrics": ["800→1200"]}]},
        {"user": "熟悉 Java 和 Redis",
         "skills": [{"group": "后端", "items": ["Java", "Redis"]}]},
    ],
    "文科·等价经历": [
        {"user": "我大三，在浙传读新闻传播",
         "slots": {"identity.grade": "大三", "identity.school": "浙传", "identity.major": "新闻传播"}},
        {"user": "想做新媒体运营",
         "slots": {"basics.target_role": "新媒体运营"}},
        {"user": "没有实习，但在校媒做过一年采编，会采写和排版，写的稿子阅读量破万",
         "deny": ["internships"],
         "exp": [{"type": "campus", "title": "校媒采编", "role": "采编记者",
                  "period": "2024-2025", "situation": "校媒内容组",
                  "actions": ["独立采写"], "results": ["阅读量破万"],
                  "metrics": ["1万+"]}],
         "skills": [{"group": "内容", "items": ["采写", "排版"]}]},
        {"user": "还参加过大学生广告赛，团队拿了省二等奖",
         "exp": [{"type": "competition", "title": "大学生广告赛", "role": "团队成员",
                  "period": "2025", "situation": "省赛",
                  "actions": ["负责文案"], "results": ["省二等奖"],
                  "metrics": ["省二"]}]},
    ],
    "跑偏·答非所问": [
        {"user": "嗯"},
        {"user": "不知道"},
        {"user": "今天天气不错"},
        {"user": "嗯嗯"},
        {"user": "你在说什么"},
        {"user": "跳过"},
        {"user": "随便吧"},
        {"user": "不想说"},
        {"user": "哦"},
        {"user": "啊？"},
        {"user": "哈哈"},
        {"user": "再见"},
    ],
}


def _build_extractor(user_text, spec):
    """按剧本声明构造抽取结果；quote 一律用用户原话，保证过红线校验。"""
    data = {}
    if spec.get("slots"):
        data["slot_updates"] = [
            {"slot": s, "value": v, "quote": user_text}
            for s, v in spec["slots"].items() if v
        ]
    if spec.get("deny"):
        data["denials"] = list(spec["deny"])
    if spec.get("exp"):
        data["experiences"] = [{**e, "quote": user_text} for e in spec["exp"]]
    if spec.get("skills"):
        data["skills"] = spec["skills"]
    return data


def run_persona(name, turns):
    """跑一个 persona，返回评测记录 dict。"""
    store = Store()
    client = httpx.Client(base_url="https://supabase.invalid/rest/v1",
                          transport=httpx.MockTransport(store))
    ctx = {"plan": {"act": "ask"}, "extract": {}}

    def fake_chat_json(msgs, **kw):
        sys_prompt = str(msgs[0])
        if "编排" in sys_prompt:
            return dict(ctx["plan"])
        if "抽取" in sys_prompt:
            return dict(ctx["extract"])
        return {}

    def fake_stream(msgs, **kw):
        yield f"（教练回应·{ctx['plan'].get('act', '?')}）"

    messages, coverages, acts, fabricated, errors = [], [], [], [], []
    updates_total = 0
    session = {"id": None, "stage": "greeting", "title": "sim"}

    with patch.object(memory, "_client", client), \
         patch.object(coach.llm, "chat_json", side_effect=fake_chat_json), \
         patch.object(coach.llm, "chat_stream", side_effect=fake_stream), \
         patch.object(coach.llm, "embed", side_effect=RuntimeError("no embed")), \
         patch.object(coach.kb, "search", return_value=[]):
        created = memory.create_session("sim-anon")
        sid = created["session"]["id"]
        session["id"] = sid
        try:
            for spec in turns:
                user_text = spec["user"]
                row = memory.get_profile(sid)
                profile_row = {"profile": row["profile"], "coverage_detail": row["coverage_detail"],
                               "version": row["version"], "coverage": row["coverage"]}
                # 确定性编排替身：与 L1 未命中时的规则兜底同口径
                ctx["plan"] = coach._rule_decide(
                    user_text, profile_row["profile"], profile_row["coverage_detail"])
                ctx["extract"] = _build_extractor(user_text, spec)
                acts.append(ctx["plan"]["act"])
                # 与路由一致：用户消息先落库进 messages，再进引擎（quote 校验依赖它）
                messages.append({"role": "user", "content": user_text})

                events = list(coach.turn_events(session, profile_row, messages, user_text))
                # --- 事件结构断言 ---
                starts = [d["id"] for e, d in events if e == "step_start"]
                ends = [d["id"] for e, d in events if e == "step_end"]
                if starts != ends:
                    errors.append(f"turn{len(messages)//2+1} step 未配对: {starts} vs {ends}")
                seqs = [d["seq"] for e, d in events if e in ("step_start", "step_end")]
                if seqs != sorted(seqs):
                    errors.append("step seq 非递增")
                prof_events = [d for e, d in events if e == "profile"]
                if prof_events:
                    c = prof_events[-1]["coverage"]
                    coverages.append(c)
                    updates_total += len(prof_events[-1].get("updates") or [])
                for e, d in events:
                    if e == "stage":
                        session["stage"] = d
                tokens = "".join(d for e, d in events if e == "token")
                messages.append({"role": "assistant", "content": tokens})
                if session["stage"] == "done":
                    break
        finally:
            client.close()

    # --- 编造核查：filled 中每条非 denied 值必须能在用户原话中找到 ---
    all_user = "".join(m["content"] for m in messages if m["role"] == "user")
    final_row = None
    client2 = httpx.Client(base_url="https://supabase.invalid/rest/v1",
                           transport=httpx.MockTransport(store))
    try:
        with patch.object(memory, "_client", client2):
            final_row = memory.get_profile(sid)
    finally:
        client2.close()
    filled = (final_row.get("coverage_detail") or {}).get("filled") or {}
    for slot, entry in filled.items():
        if isinstance(entry, dict) and entry.get("denied"):
            continue
        raw = str(entry.get("value", "") if isinstance(entry, dict) else entry)
        parts = [coach._norm(p) for p in re.split(r"[、，,/\s]+", raw) if p.strip()]
        for v in parts:
            if v and v not in coach._norm(all_user):
                fabricated.append(f"{slot}={v}")
    # 注：经历 title/标签是模型归纳词，红线约束在 quote 证据上（合并时已强制校验），
    # 此处只核查"写进画像的声明值"能否在用户原话中找到。

    final_cov = final_row.get("coverage", coverages[-1] if coverages else 0)
    monotone = all(b >= a for a, b in zip(coverages, coverages[1:]))
    return {
        "name": name, "turns": len([m for m in messages if m["role"] == "user"]),
        "acts": acts, "coverages": coverages, "final_cov": final_cov,
        "gaps": [s.id for s in coach._load_tpl().interview_slots
                 if s.required and not coach._slot_filled(
                     s, final_row["profile"], final_row["coverage_detail"])],
        "monotone": monotone, "fabricated": fabricated, "errors": errors,
        "updates": updates_total, "stage": session["stage"],
        "profile": final_row["profile"],
    }


EXPECT = {
    "大一·零经历": {"min_cov": 0.8, "done": True},
    "大二·有项目": {"min_cov": 0.8, "done": True},
    "大三·有实习": {"min_cov": 0.8, "done": True},
    "文科·等价经历": {"min_cov": 0.8, "done": True},
    "跑偏·答非所问": {"min_cov": None, "done": True, "expect_gaps": True},
}


def evaluate():
    results, all_pass = [], True
    for name, turns in PERSONAS.items():
        r = run_persona(name, turns)
        exp = EXPECT[name]
        checks = []
        checks.append(("事件配对/有序", not r["errors"]))
        checks.append(("覆盖率单调不减", r["monotone"]))
        checks.append(("零编造", not r["fabricated"]))
        checks.append(("≤12 轮", r["turns"] <= 12))
        checks.append(("终态 done", r["stage"] == "done"))
        if exp["min_cov"] is not None:
            checks.append((f"覆盖≥{exp['min_cov']}", r["final_cov"] >= exp["min_cov"]))
        if exp.get("expect_gaps"):
            checks.append(("收尾仍列缺口", bool(r["gaps"])))
        r["checks"] = checks
        r["pass"] = all(ok for _, ok in checks)
        all_pass = all_pass and r["pass"]
        results.append(r)
    return results, all_pass


def _fmt_covs(cs):
    return " → ".join(f"{c:.2f}" for c in cs) or "—"


def write_report(results, all_pass):
    lines = [
        "# 知脉 v2 · 评测报告",
        "",
        f"> 生成：tests/coach_sim.py（确定性替身，可复跑）· 基线 dev/v2",
        "",
        "## 访谈模拟（5 persona · 引擎级）",
        "",
        "| Persona | 轮数 | 覆盖率轨迹 | 终态 | 编造 | 判定 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        verdict = "✅" if r["pass"] else "❌"
        fab = "0" if not r["fabricated"] else ",".join(r["fabricated"])
        lines.append(
            f"| {r['name']} | {r['turns']} | {_fmt_covs(r['coverages'])} "
            f"| {r['stage']} | {fab} | {verdict} |")
    lines += [
        "",
        "断言口径：step_start/end 配对且 seq 递增；profile.coverage 单调不减；",
        "coverage_detail.filled 每条非 denied 值必须能在用户原话中找到（零编造红线）；",
        "成功 persona ≤12 轮收敛至 stage=done 且 coverage≥0.8；跑偏 persona 到 12 轮上限收尾并列出缺口。",
        "",
        "安全/契约断言（鉴权 404、限流 429、幂等去重、SSE 事件序、终端 done）见 `tests/test_coach.py`。",
        "",
        "## 逐项结果",
        "",
    ]
    for r in results:
        lines.append(f"### {r['name']}")
        for label, ok in r["checks"]:
            lines.append(f"- {'✅' if ok else '❌'} {label}")
        if r["errors"]:
            lines.append(f"- 异常：{'; '.join(r['errors'])}")
        lines.append("")
    lines.append(f"**总体：{'全部通过' if all_pass else '存在失败项'}**")
    lines.append("")
    out = ROOT / "docs" / "评测报告-v2.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


class CoachSimTest(unittest.TestCase):
    def test_personas_converge_and_stay_honest(self):
        results, all_pass = evaluate()
        for r in results:
            with self.subTest(persona=r["name"]):
                self.assertTrue(r["pass"], f"{r['name']}: {r['checks']} {r['errors']}")
        self.assertTrue(all_pass)


if __name__ == "__main__":
    results, all_pass = evaluate()
    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"[{status}] {r['name']}: turns={r['turns']} cov={_fmt_covs(r['coverages'])} "
              f"stage={r['stage']} gaps={r['gaps']} acts={r['acts']}")
    out = write_report(results, all_pass)
    print(f"\n报告已写入 {out}")
    sys.exit(0 if all_pass else 1)

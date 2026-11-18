#!/usr/bin/env python3
"""#8/#9/#23 真模型回放：把 5 个脚本 persona 打进**已部署**的 v2 访谈链路，产出可人工评分的转写与机检表。

与 tests/coach_sim.py 的分工：coach_sim 用确定性替身离线验证引擎编排骨架（可进 CI）；
本脚本打真模型真检索真库，验证「换上真的之后还成立吗」，并生成 §13「访谈质量」行需要的人工 rubric。

用法（会计费，必须显式带 --yes-billable）：
  python tests/v2_replay.py --base https://<fc-host> --yes-billable
  python tests/v2_replay.py --base https://<fc-host> --yes-billable --personas 大三·有实习

产物：docs/v2-真模型回放.md（转写 + 机检 + 待人工打分的 rubric）。
"""
import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.coach_sim import EXPECT, PERSONAS  # 剧本与期望只有一份来源

MAX_TURNS = 12
DUP_SIMILARITY = 0.72
# 来源解耦红线：产品内所有来源链接只允许出现公开镜像域名，原站域名一律视为泄漏
MIRROR_HOST = "lzjzj-creator.github.io"


def norm(text):
    return re.sub(r"[\s、，,。.！!？?：:；;（）()「」“”\"'·\-—]+", "", str(text or ""))


def bigrams(text):
    text = norm(text)
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}


def similarity(a, b):
    left, right = bigrams(a), bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def stream_turn(client, base, anon, token, session_id, message, findings):
    """跑一轮，返回 {question, coverage, stage, steps, error}。"""
    payload = {"session_id": session_id, "client_msg_id": str(uuid.uuid4()), "message": message}
    headers = {"X-Anon-Id": anon}
    if token:
        headers["X-Session-Token"] = token
    out = {"question": "", "coverage": None, "stage": None, "steps": [], "error": None,
           "sources": [], "status": None}
    with client.stream("POST", f"{base}/api/v2/chat/stream", headers=headers, json=payload) as response:
        out["status"] = response.status_code
        if response.status_code != 200:
            out["error"] = f"HTTP {response.status_code}"
            return out
        pending = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                pending = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except ValueError:
                findings.append("SSE data 帧不是合法 JSON")
                continue
            name = pending
            if name == "session":
                out["session_id"] = data.get("session_id")
                out["session_token"] = data.get("session_token")
                out["stage"] = data.get("stage")
            elif name == "token":
                out["question"] += data if isinstance(data, str) else str(data.get("text", ""))
            elif name == "profile":
                out["coverage"] = data.get("coverage")
                out["gaps"] = data.get("gaps")
            elif name in ("step_start", "step_end"):
                out["steps"].append((name, (data.get("title") or data.get("id") or "")))
            elif name == "sources":
                out["sources"].extend(data or [])
            elif name == "done":
                out["stage"] = data.get("stage")
                out["coverage"] = data.get("coverage", out["coverage"])
                out["message_id"] = data.get("message_id")
            elif name == "error":
                out["error"] = data.get("code")
    return out


def persona_transcript(client, base, name, script, findings):
    anon = str(uuid.uuid4())
    first = stream_turn(client, base, anon, None, None, "", findings)   # 建会话 + 问候，不调模型
    if first.get("error"):
        return {"name": name, "aborted": first["error"], "turns": []}
    sid, token = first.get("session_id"), first.get("session_token")
    if not token:      # 已建会话但未回令牌（非首轮）→ 无法继续，如实记失败
        return {"name": name, "aborted": "session 事件未下发令牌", "turns": []}
    turns = [{"user": "", "assistant": first["question"], "coverage": first.get("coverage")}]
    for step in script:
        if len([t for t in turns if t["user"]]) >= MAX_TURNS:
            break
        result = stream_turn(client, base, anon, token, sid, step["user"], findings)
        if result.get("error"):
            turns.append({"user": step["user"], "assistant": "", "error": result["error"]})
            break
        turns.append({"user": step["user"], "assistant": result["question"],
                      "coverage": result.get("coverage"), "stage": result.get("stage"),
                      "sources": result.get("sources")})
        if result.get("stage") == "done":
            break
    bundle = client.get(f"{base}/api/v2/sessions/{sid}",
                        headers={"X-Anon-Id": anon, "X-Session-Token": token})
    profile = {}
    coverage = None
    stage = None
    if bundle.status_code == 200:
        body = bundle.json()
        profile = ((body.get("profile") or {}).get("profile")) or {}
        coverage = (body.get("profile") or {}).get("coverage")
        stage = (body.get("session") or {}).get("stage")
    return {"name": name, "session_id": sid, "turns": turns, "profile": profile,
            "coverage": coverage, "stage": stage}


def machine_checks(record, findings):
    """能机检的部分先机检；「有承接」这类留给 rubric。"""
    if record.get("aborted"):
        return [("可跑通", False, record["aborted"])], []
    checks = []
    user_texts = [t["user"] for t in record["turns"] if t["user"]]
    questions = [t["assistant"] for t in record["turns"] if t["user"]]
    coverages = [t["coverage"] for t in record["turns"] if t["coverage"] is not None]

    checks.append(("剧本用完或提前收尾", len(user_texts) >= len(PERSONAS[record["name"]])
                   or record["stage"] == "done", f"{len(user_texts)} 轮 / stage={record['stage']}"))
    checks.append(("轮数 ≤12", len(user_texts) <= MAX_TURNS, f"{len(user_texts)} 轮"))
    checks.append(("终态 done", record["stage"] == "done", f"stage={record['stage']}"))
    checks.append(("覆盖率单调不减", all(b >= a - 1e-9 for a, b in zip(coverages, coverages[1:])),
                   " → ".join(f"{c:.2f}" for c in coverages) or "无 profile 事件"))
    over = [q for q in questions if q.count("？") + q.count("?") > 1]
    checks.append(("每轮 ≤1 问", not over, over and over[0][:40] or ""))
    dupes = []
    for i in range(len(questions)):
        for j in range(i + 1, len(questions)):
            if similarity(questions[i], questions[j]) > DUP_SIMILARITY:
                dupes.append(f"第{i + 1}/{j + 1}问相似 {similarity(questions[i], questions[j]):.2f}")
    checks.append(("提问不重复", not dupes, "; ".join(dupes[:2])))
    # 零编造红线：写进画像的声明值必须能在用户原话里找到
    all_user = norm("".join(user_texts))
    fabricated = []
    for path, value in _flatten(profile_of(record)):
        for piece in [norm(p) for p in re.split(r"[、，,/\s]+", str(value)) if norm(p)]:
            if piece and piece not in all_user and not _derived_ok(path, piece, all_user):
                fabricated.append(f"{path}={piece}")
    checks.append(("画像零编造", not fabricated, "; ".join(fabricated[:3])))
    mirror = sorted({urlsplit(s.get("url", "")).netloc for s in
                     sum((t.get("sources") or [] for t in record["turns"]), [])
                     if s.get("url")} - {MIRROR_HOST})
    checks.append(("来源均为镜像域名", not mirror, "; ".join(mirror[:3])))
    expected = EXPECT[record["name"]]
    if expected["min_cov"] is not None and record["coverage"] is not None:
        checks.append((f"覆盖≥{expected['min_cov']}", record["coverage"] >= expected["min_cov"],
                       f"{record['coverage']:.2f}"))
    return checks, questions


def _flatten(profile, prefix=""):
    for key, value in (profile or {}).items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            if "value" in value:
                yield path, value["value"]
            else:
                yield from _flatten(value, path + ".")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from _flatten(item, path + ".")
                else:
                    yield path, item
        else:
            yield path, value


_EXPERIENCE_TYPES = {"internship", "project", "campus", "competition", "opensource", "research"}


def _derived_ok(path, piece, all_user):
    """经历条数、时间归并、分组/类型标签由系统推导，不算编造。"""
    if path.endswith((".count", "period", ".period")) or piece in {"无"}:
        return True
    # skills.group 是系统分组名；type/star_score 是契约枚举与系统评分（与 backend/coach.py 同源）
    if path.endswith("skills.group") or path.endswith("experiences.star_score"):
        return True
    return path.endswith("experiences.type") and piece in _EXPERIENCE_TYPES


def profile_of(record):
    return record.get("profile") or {}


def render(records, findings):
    lines = ["# v2 真模型回放（人工 rubric + 机检）", "",
             "> 由 `tests/v2_replay.py` 对已部署链路生成，逐轮记录真实模型输出。",
             "> 机检列是脚本断言；`评分` 三列必须人工填写（§13 通过线：8/10 通过，LLM 评审仅辅助）。",
             ""]
    total_pass = total_all = 0
    for record in records:
        lines.append(f"## {record['name']}")
        if record.get("aborted"):
            lines += ["", f"**中止**：{record['aborted']}", ""]
            continue
        checks, questions = machine_checks(record, findings)
        lines += ["", "| 机检项 | 结果 | 备注 |", "|---|---|---|"]
        for name, ok, detail in checks:
            total_pass += int(bool(ok))
            total_all += 1
            lines.append(f"| {name} | {'✅' if ok else '❌'} | {detail or ''} |")
        lines += ["", "### 转写", "", "| # | 用户 | 助手 | 覆盖率 | 有承接 | 不重复 | 单问 |",
                  "|---|---|---|---|---|---|---|"]
        for index, turn in enumerate(record["turns"], start=1):
            cov = f"{turn['coverage']:.2f}" if isinstance(turn.get("coverage"), (int, float)) else ""
            user = (turn["user"] or "（开场）").replace("|", "｜")
            bot = re.sub(r"\s+", " ", turn["assistant"] or "").replace("|", "｜")
            lines.append(f"| {index} | {user} | {bot} | {cov} | ☐ | ☐ | ☐ |")
        lines += ["", f"终态 stage=`{record['stage']}` · 覆盖率 `{record['coverage']}` · "
                      f"画像 `{json.dumps(profile_of(record), ensure_ascii=False)[:400]}`", ""]
    lines += ["## 汇总", "",
              f"- 机检：{total_pass}/{total_all} 通过",
              f"- 人工 rubric：待填（每 persona 逐轮三列，通过线 8/10）",
              f"- 泄漏扫描：{len(findings)} 处异常", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--yes-billable", action="store_true",
                        help="确认这是真模型调用（会产生费用）")
    parser.add_argument("--personas", default="", help="逗号分隔的 persona 名子集")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs" / "v2-真模型回放.md"))
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if not args.yes_billable:
        print("需要 --yes-billable：本脚本会打真模型，产生费用。")
        return 2

    names = [n.strip() for n in args.personas.split(",") if n.strip()] or list(PERSONAS)
    unknown = [n for n in names if n not in PERSONAS]
    if unknown:
        print(f"未知 persona：{unknown}；可选：{list(PERSONAS)}")
        return 2
    base = args.base.rstrip("/")
    findings, records = [], []
    health = httpx.get(f"{base}/health", timeout=30)
    if health.status_code != 200:
        print(f"目标不可用：/health HTTP {health.status_code}")
        return 2
    with httpx.Client(timeout=args.timeout) as client:
        for name in names:
            print(f"▶ {name} …", flush=True)
            started = time.perf_counter()
            records.append(persona_transcript(client, base, name, PERSONAS[name], findings))
            print(f"   完成 {time.perf_counter() - started:.1f}s")
            time.sleep(1)
    text = render(records, findings)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"\n写入 {args.out}")
    failed = [line for line in text.splitlines() if line.startswith("| ") and " ❌ " in line]
    for line in failed:
        print("  " + line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

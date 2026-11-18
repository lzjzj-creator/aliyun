#!/usr/bin/env python3
"""线上真机验收：#18 autofill（evidence_map/gaps）+ #9 optimize（plan/section 计时）。

打真实部署、产生模型费用。与 v2_smoke（零成本契约）和 v2_replay（访谈质量）互补：
本脚本验证「访谈会话 → 一键填写 → 分段优化」整链在线上可跑通，且证据链/守卫成立。

用法：
  python tests/v2_accept.py --base https://<fc-host> --yes-billable [--out docs/v2-线上验收.json]
"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

PROBE = [
    "我大二，在浙大读软件工程",
    "想找后端开发实习",
    "做过二手书小程序，我负责后端，用 Java 和 MySQL，上线后日活涨了30%",
]


def read_stream(response):
    names, session, pending = [], None, None
    for line in response.iter_lines():
        if line.startswith("event:"):
            pending = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        name = pending or "?"
        names.append(name)
        if name == "session":
            session = json.loads(line[5:].strip())
    return names, session or {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--yes-billable", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    if not args.yes_billable:
        print("需要 --yes-billable：本脚本会打真模型，产生费用。")
        return 2

    base = args.base.rstrip("/")
    anon = str(uuid.uuid4())
    checks, timings = [], {}

    def record(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
        print(f"  {'PASS' if ok else 'FAIL'} {name} {detail}")

    with httpx.Client(base_url=base, timeout=args.timeout) as client:
        health = client.get("/health")
        record("GET /health", health.status_code == 200, health.status_code)

        headers = {"X-Anon-Id": anon}
        with client.stream("POST", "/api/v2/chat/stream", headers=headers, json={
                "session_id": None, "client_msg_id": str(uuid.uuid4()), "message": ""}) as resp:
            record("建会话", resp.status_code == 200, resp.status_code)
            _, greeting = read_stream(resp)
        session = greeting or {}
        sid, token = session.get("session_id", ""), session.get("session_token", "")
        record("拿到会话令牌", bool(sid and token))
        owned = {"X-Anon-Id": anon, "X-Session-Token": token}

        for text in (PROBE if sid and token else []):
            started = time.perf_counter()
            with client.stream("POST", "/api/v2/chat/stream", headers=owned, json={
                    "session_id": sid, "client_msg_id": str(uuid.uuid4()), "message": text}) as resp:
                ok = resp.status_code == 200
                names, payload = read_stream(resp) if ok else ([], {})
            timings.setdefault("访谈轮", []).append(round(time.perf_counter() - started, 2))
            record(f"访谈轮 {text[:8]}…", ok and "done" in names, f"{timings['访谈轮'][-1]}s")

        if not (sid and token):
            return finish(checks, timings, args)

        bundle = client.get(f"/api/v2/sessions/{sid}", headers=owned)
        record("读回会话包", bundle.status_code == 200, bundle.status_code)
        bundle = bundle.json() if bundle.status_code == 200 else {}
        messages = bundle.get("messages") or []
        user_text = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
        record("会话含 3 轮用户消息", sum(1 for m in messages if m.get("role") == "user") >= 3)

        tpl = client.get("/api/v2/resume/template")
        record("GET template", tpl.status_code == 200, tpl.status_code)
        version = (tpl.json() or {}).get("template_version", "") if tpl.status_code == 200 else ""
        record("模板版本非空", bool(version), version)

        started = time.perf_counter()
        fill = client.post("/api/v2/resume/autofill", headers=owned, json={
            "session_id": sid, "template_version": version})
        fill_s = round(time.perf_counter() - started, 2)
        timings["autofill"] = [fill_s]
        record("POST autofill", fill.status_code == 200, f"HTTP {fill.status_code} / {fill_s}s")
        data = fill.json() if fill.status_code == 200 else {}
        fields = data.get("fields") or {}
        evidence = data.get("evidence_map") or {}
        gaps = data.get("gaps") or []

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield from walk(value, f"{path}.{key}" if path else key)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    yield from walk(value, f"{path}.{index}")
            elif isinstance(node, str) and node.strip():
                yield path, node

        leaves = list(walk(fields))
        record("autofill 产出非空", bool(leaves), len(leaves))
        bad = [p for p, v in leaves if v not in user_text and not any(v in q.get("quote", "") for q in evidence.get(p, []))]
        record("非证据字段已清空（0 无据填写）", not bad, bad[:3])
        bad_quote = {p: [e["quote"] for e in evs if e.get("quote") and e["quote"] not in user_text]
                     for p, evs in evidence.items()}
        bad_quote = {p: q for p, q in bad_quote.items() if q}
        record("evidence quote 逐字可溯源", not bad_quote, list(bad_quote)[:3])
        record("gaps 有输出", bool(gaps), len(gaps))

        plan = client.post("/api/v2/resume/optimize/plan", headers=headers, json={
            "template_version": version, "fields": fields})
        record("POST optimize/plan", plan.status_code == 200, plan.status_code)
        plan_data = plan.json() if plan.status_code == 200 else {}
        sections = plan_data.get("sections") or plan_data.get("plan") or []
        record("plan 含可优化区块", bool(sections), len(sections) if isinstance(sections, list) else type(sections))

        target = "projects" if fields.get("projects") else ("summary" if fields.get("summary") else "")
        record("有可优化目标区块", bool(target), target)
        if target:
            started = time.perf_counter()
            opt = client.post("/api/v2/resume/optimize/section", headers=headers, json={
                "template_version": version, "section": target, "fields": fields,
                "confirmed_facts": ""})
            opt_s = round(time.perf_counter() - started, 2)
            timings.setdefault("optimize/section", []).append(opt_s)
            record("POST optimize/section", opt.status_code == 200, f"HTTP {opt.status_code} / {opt_s}s")
            opt_data = opt.json() if opt.status_code == 200 else {}
            record("section 返回终态", opt_data.get("status") in {"ok", "skipped", "done"},
                   opt_data.get("status"))
            new_numbers = set()
            before = json.dumps(opt_data.get("original", ""), ensure_ascii=False)

            def numbers(text):
                import re
                return set(re.findall(r"\d+(?:\.\d+)?%?", str(text)))

            for _, text in walk(opt_data.get("optimized")):
                new_numbers |= numbers(text) - numbers(user_text) - numbers(before)
            record("optimize 无编造数字", not new_numbers, sorted(new_numbers)[:5])
            if opt_data.get("placeholders"):
                record("缺证据输出去占位", True, len(opt_data["placeholders"]))

        delete = client.delete(f"/api/v2/sessions/{sid}", headers=owned)
        record("DELETE 会话", delete.status_code == 200, delete.status_code)

    return finish(checks, timings, args)


def finish(checks, timings, args):
    failed = [c for c in checks if not c["ok"]]
    print(f"\n结论：{len(checks) - len(failed)}/{len(checks)} 通过")
    for c in failed:
        print(f"  FAIL {c['name']}（{c['detail']}）")
    if args.out:
        Path(args.out).write_text(json.dumps({
            "base": args.base, "ran_at": int(time.time()),
            "checks": checks, "timings": timings,
            "failed": [c["name"] for c in failed]}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细写入 {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

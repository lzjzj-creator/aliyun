#!/usr/bin/env python3
"""RAG 端到端评测：10 条知识问题 → 来源非空 + 最高相关分 > 0.25。

用法：
  python tests/rag_e2e.py
  python tests/rag_e2e.py --base http://127.0.0.1:8788
  python tests/rag_e2e.py --json-out docs/rag-eval.json
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

DEFAULT_BASE = "https://zhimai-api-hiwijfvopw.cn-beijing.fcapp.run"
MIN_SCORE = 0.25
FORBIDDEN_HOST = "zhimai-rag-knowledge.pages.dev"

QUESTIONS = [
    "大三找实习该怎么准备？",
    "大一应该重点补什么信息差？",
    "大四秋招什么时候开始准备？",
    "简历上的项目经历怎么写才有说服力？",
    "没有实习经历简历怎么写？",
    "保研需要准备哪些材料？",
    "出国留学怎么规划申请？",
    "考公应该从什么时候开始准备？",
    "怎么提高大学绩点？",
    "转专业需要做哪些准备？",
]


def sse_events(raw: str):
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        event, data_lines = None, []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if event:
            payload = "\n".join(data_lines)
            try:
                yield event, json.loads(payload) if payload else None
            except json.JSONDecodeError:
                yield event, payload


def ask(client: httpx.Client, base: str, query: str) -> dict:
    with client.stream("POST", f"{base}/api/chat/stream",
                       json={"query": query, "has_attachment": False, "history": []}) as r:
        r.raise_for_status()
        raw = r.read().decode("utf-8", errors="replace")

    route, sources, answer, errors = None, [], "", []
    for event, data in sse_events(raw):
        if event == "route":
            route = data
        elif event == "sources":
            sources = data or []
        elif event == "token":
            answer += data or ""
        elif event == "error":
            errors.append(data)
    top = max((float(s.get("score", 0)) for s in sources), default=0.0)
    hosts = sorted({urlsplit(s.get("url", "")).hostname or "" for s in sources if s.get("url")})
    return {
        "query": query,
        "route": route,
        "sources": sources,
        "source_count": len(sources),
        "top_score": round(top, 3),
        "hosts": hosts,
        "answer_chars": len(answer),
        "answer_head": answer[:120],
        "errors": errors,
        "ok": bool(sources) and top > MIN_SCORE and not errors
              and FORBIDDEN_HOST not in hosts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    results = []
    with httpx.Client(trust_env=False, timeout=args.timeout) as client:
        for query in QUESTIONS:
            try:
                result = ask(client, base, query)
            except httpx.HTTPError as e:
                print(f"请求失败：{query} → {e}", file=sys.stderr)
                return 2
            results.append(result)
            route = result["route"] or {}
            mark = "PASS" if result["ok"] else "FAIL"
            print(f"[{mark}] {query}")
            print(f"       intent={route.get('intent')} scope={route.get('kb_scope')} "
                  f"sources={result['source_count']} top={result['top_score']:.3f} "
                  f"answer={result['answer_chars']}字")
            if result["hosts"]:
                print(f"       host={', '.join(result['hosts'])}")
            if result["errors"]:
                print(f"       error={result['errors']}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\nRAG 端到端：{passed}/{len(results)} 通过（阈值 top_score > {MIN_SCORE}）")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base": base,
        "min_score": MIN_SCORE,
        "total": len(results),
        "passed": passed,
        "results": results,
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"JSON 报告：{args.json_out}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
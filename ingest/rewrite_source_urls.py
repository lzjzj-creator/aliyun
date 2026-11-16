# -*- coding: utf-8 -*-
"""把 documents / chunks 里的 source_url 统一改写到 KB_SOURCE_BASE（知识库镜像站）。

用法：
  KB_SOURCE_BASE=https://镜像站地址 python ingest/rewrite_source_urls.py --dry-run   # 只看会改多少行
  KB_SOURCE_BASE=https://镜像站地址 python ingest/rewrite_source_urls.py             # 真正改写（幂等，可重复跑）
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.kb_source import to_mirror_url  # noqa: E402

load_dotenv(ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
BASE = os.getenv("KB_SOURCE_BASE", "").rstrip("/")


def mirror_url(old: str) -> str:
    return to_mirror_url(old, BASE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not BASE:
        sys.exit("请先设置 KB_SOURCE_BASE（镜像站地址）")

    with httpx.Client(
        base_url=f"{SUPABASE_URL}/rest/v1",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"},
        timeout=120,
    ) as sb:
        for table, key in (("documents", "slug"), ("chunks", "id")):
            offset = 0
            pending = []
            while True:
                r = sb.get(f"/{table}", params={
                    "select": f"{key},source_url", "limit": 1000, "offset": offset})
                r.raise_for_status()
                rows = r.json()
                for row in rows:
                    new = mirror_url(row.get("source_url") or "")
                    if new != (row.get("source_url") or ""):
                        pending.append((row[key], new))
                if len(rows) < 1000:
                    break
                offset += 1000
            print(f"{table}: {'待改写' if args.dry_run else '待写入'} {len(pending)} 行", flush=True)
            if args.dry_run:
                continue

            def write(item):
                pk, url = item
                w = sb.patch(f"/{table}", params={key: f"eq.{pk}"}, json={"source_url": url})
                w.raise_for_status()

            done = 0
            with ThreadPoolExecutor(max_workers=8) as pool:
                for _ in pool.map(write, pending):
                    done += 1
                    if done % 500 == 0:
                        print(f"{table}: 已写入 {done}/{len(pending)}", flush=True)
            print(f"{table}: 已改写 {done} 行", flush=True)


if __name__ == "__main__":
    main()

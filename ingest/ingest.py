# -*- coding: utf-8 -*-
"""
知脉知识库入库脚本：contentIndex.json → 清洗 → 切段 → embedding → Supabase

用法：
  pip install -r requirements.txt
  python ingest.py --dry-run          # 只统计，不联网（验证清洗/切分逻辑）
  python ingest.py                    # 增量入库（已入库的文档/分段自动跳过，断点续传）
  python ingest.py --wipe             # 清空后全量重灌
  python ingest.py --limit 50         # 只跑前 50 篇试水

断点续传：
  - embedding 结果逐批写入 data/embed_cache.jsonl，重跑时不再调用 API
  - documents / chunks 写入前查询已存在的 key，只补缺的
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "contentIndex.json"
CACHE = ROOT / "data" / "embed_cache.jsonl"
BASE_URL = os.getenv("KB_SOURCE_BASE", "http://127.0.0.1:4310").rstrip("/")

load_dotenv(ROOT / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
DASHSCOPE_KEY = os.getenv("DASHSCOPE_API_KEY", "")

EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBED_BATCH = 10          # text-embedding-v4 单次上限
DOC_BATCH = 200           # documents 无向量，批量可大
CHUNK_BATCH = 100         # 含 1024 维向量，批量大了会被断连
MAX_CHUNK = 500           # 每段目标字符数
OVERLAP = 80              # 段间重叠

SCOPE_MAP = {
    "0001": "freshman",
    "0002": "sophomore_junior",
    "0003": "senior",
    "1001": "cases",
    "1002": "major",
    "1003": "video",
}
SKIP_TAGS = {"自动目录", "索引"}
SKIP_TITLE_RE = re.compile(r"目录索引|全库图谱|关系图谱")
MIN_CONTENT = 100


def kb_scope(slug: str) -> str:
    m = re.match(r"^(\d{4})", slug)
    return SCOPE_MAP.get(m.group(1), "meta") if m else "meta"


def source_url(slug: str) -> str:
    from urllib.parse import quote
    return f"{BASE_URL}/{quote(slug)}/index.html"


def is_article(item: dict) -> bool:
    """过滤目录索引页、图谱页、过短内容，只留真文章。"""
    title = item.get("title", "")
    content = (item.get("content") or "").strip()
    if SKIP_TITLE_RE.search(title):
        return False
    if SKIP_TAGS & set(item.get("tags") or []):
        return False
    return len(content) >= MIN_CONTENT


def chunk_text(text: str) -> list[str]:
    """按空行分段聚合到 ~500 字，超长段硬切，段间留 80 字重叠。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 2 <= MAX_CHUNK:
            buf = f"{buf}\n\n{p}" if buf else p
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        while len(p) > MAX_CHUNK:
            chunks.append(p[:MAX_CHUNK])
            p = p[MAX_CHUNK - OVERLAP:]
        buf = p
    if buf:
        chunks.append(buf)
    return chunks


def chunk_key(c: dict) -> str:
    return f"{c['doc_slug']}#{c['seq']}"


def embed_batch(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    """DashScope 原生 embedding 接口，失败重试 3 次。"""
    url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
    body = {"model": EMBED_MODEL, "input": {"texts": texts},
            "parameters": {"text_type": "document"}}
    headers = {"Authorization": f"Bearer {DASHSCOPE_KEY}"}
    for attempt in range(3):
        try:
            r = client.post(url, json=body, headers=headers, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            out = r.json()["output"]["embeddings"]
            out.sort(key=lambda e: e["text_index"])
            return [e["embedding"] for e in out]
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            wait = 2 ** attempt * 2
            print(f"  embedding 重试 {attempt + 1}: {e}，{wait}s 后重试", flush=True)
            time.sleep(wait)
    return []  # unreachable


def load_embed_cache() -> dict:
    cache = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                cache[row["key"]] = row["vec"]
    return cache


def append_embed_cache(key: str, vec: list):
    with CACHE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "vec": vec}) + "\n")


def sb_client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{SUPABASE_URL}/rest/v1",
        headers={"apikey": SUPABASE_KEY,
                 "Authorization": f"Bearer {SUPABASE_KEY}",
                 "Content-Type": "application/json",
                 "Prefer": "return=minimal"},
        timeout=120,
    )


def post_retry(sb: httpx.Client, path: str, rows: list, label: str):
    """批量写入，断连/5xx 自动重试 4 次。"""
    for attempt in range(4):
        try:
            r = sb.post(path, json=rows, timeout=180)
            if r.status_code < 300:
                return
            err = f"HTTP {r.status_code}: {r.text[:300]}"
        except httpx.HTTPError as e:
            err = str(e)
        if attempt == 3:
            raise RuntimeError(f"{label} 写入失败: {err}")
        wait = 2 ** attempt * 3
        print(f"  {label} 重试 {attempt + 1}: {err}，{wait}s 后重试", flush=True)
        time.sleep(wait)


def fetch_existing_keys(sb: httpx.Client, path: str, select: str) -> set:
    """分页拉全表 key（PostgREST 单页上限 1000 行）。"""
    keys, offset = set(), 0
    while True:
        r = sb.get(path, params={"select": select, "limit": 1000, "offset": offset}, timeout=120)
        r.raise_for_status()
        rows = r.json()
        for row in rows:
            keys.add(tuple(row.values()))
        if len(rows) < 1000:
            return keys
        offset += 1000


def wipe(sb: httpx.Client):
    r = sb.delete("/chunks", params={"id": "gte.0"})
    r.raise_for_status()
    r = sb.delete("/documents", params={"slug": "neq."})
    r.raise_for_status()
    print("已清空旧数据")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不联网")
    ap.add_argument("--wipe", action="store_true", help="清空后全量重灌")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    raw = json.loads(DATA.read_text(encoding="utf-8"))
    docs = [
        {"slug": s, "title": v.get("title", s), "dir": s.split("/")[0],
         "kb_scope": kb_scope(s), "tags": v.get("tags") or [],
         "source_url": source_url(s),
         "char_count": len(v["content"]), "_content": v["content"].strip()}
        for s, v in raw.items()
        if is_article(v)
    ]
    docs.sort(key=lambda d: d["slug"])
    if args.limit:
        docs = docs[: args.limit]

    all_chunks = []
    for d in docs:
        for i, c in enumerate(chunk_text(d["_content"])):
            all_chunks.append({"doc_slug": d["slug"], "seq": i,
                               "text": f"《{d['title']}》\n{c}",
                               "kb_scope": d["kb_scope"],
                               "visibility": "public", "status": "ready",
                               "source_url": d["source_url"]})

    from collections import Counter
    dist = Counter(d["kb_scope"] for d in docs)
    print(f"文章 {len(docs)} 篇 → 分段 {len(all_chunks)} 个")
    print(f"kb_scope 分布: {dict(dist)}")
    if args.dry_run:
        print("dry-run 结束，未联网。")
        return

    if not (SUPABASE_URL and SUPABASE_KEY and DASHSCOPE_KEY):
        sys.exit("缺少 SUPABASE_URL / SUPABASE_SERVICE_KEY / DASHSCOPE_API_KEY，请填 .env")

    with httpx.Client(timeout=60) as emb, sb_client() as sb:
        if args.wipe:
            wipe(sb)

        # 1) documents：只补缺的
        existing_docs = fetch_existing_keys(sb, "/documents", "slug")
        todo_docs = [{k: v for k, v in d.items() if k != "_content"}
                     for d in docs if (d["slug"],) not in existing_docs]
        for i in range(0, len(todo_docs), DOC_BATCH):
            post_retry(sb, "/documents", todo_docs[i:i + DOC_BATCH], "documents")
        print(f"documents 新增 {len(todo_docs)} 行（已存在 {len(existing_docs)}）", flush=True)

        # 2) embedding：缓存命中跳过 API，未命中边算边落盘
        cache = load_embed_cache()
        pending = [c for c in all_chunks if chunk_key(c) not in cache]
        print(f"embedding 待算 {len(pending)} 个（缓存命中 {len(all_chunks) - len(pending)}）", flush=True)
        done = 0
        for i in range(0, len(pending), EMBED_BATCH):
            batch = pending[i:i + EMBED_BATCH]
            vecs = embed_batch(emb, [c["text"] for c in batch])
            for c, v in zip(batch, vecs):
                key = chunk_key(c)
                cache[key] = v
                append_embed_cache(key, v)
            done += len(batch)
            if done % 100 < EMBED_BATCH or done == len(pending):
                print(f"embedding {done}/{len(pending)}", flush=True)

        for c in all_chunks:
            v = cache[chunk_key(c)]
            c["embedding"] = "[" + ",".join(f"{x:.6f}" for x in v) + "]"

        # 3) chunks：只补缺的
        existing_chunks = fetch_existing_keys(sb, "/chunks", "doc_slug,seq")
        todo_chunks = [c for c in all_chunks
                       if (c["doc_slug"], c["seq"]) not in existing_chunks]
        print(f"chunks 待写 {len(todo_chunks)} 个（已存在 {len(existing_chunks)}）", flush=True)
        for i in range(0, len(todo_chunks), CHUNK_BATCH):
            post_retry(sb, "/chunks", todo_chunks[i:i + CHUNK_BATCH], "chunks")
            done = i + len(todo_chunks[i:i + CHUNK_BATCH])
            if done % 500 < CHUNK_BATCH or done == len(todo_chunks):
                print(f"chunks 写入 {done}/{len(todo_chunks)}", flush=True)
        print(f"完成：chunks 在库 {len(existing_chunks) + len(todo_chunks)} 个分段。")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
知脉知识库入库脚本：contentIndex.json → 清洗 → 切段 → embedding → Supabase

用法：
  pip install -r requirements.txt
  python ingest.py --dry-run          # 只统计，不联网（验证清洗/切分逻辑）
  python ingest.py                    # 全量入库（需要 .env 里的 SUPABASE_*/DASHSCOPE_*）
  python ingest.py --limit 50         # 只跑前 50 篇试水
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

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "contentIndex.json"
BASE_URL = "https://zhimai-rag-knowledge.pages.dev"

load_dotenv(ROOT / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
DASHSCOPE_KEY = os.getenv("DASHSCOPE_API_KEY", "")

EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBED_BATCH = 25          # DashScope 单次上限
INSERT_BATCH = 200        # PostgREST 批量插入
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
    return f"{BASE_URL}/{quote(slug)}/"


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


def embed_batch(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    """DashScope 原生 embedding 接口，失败重试 3 次。"""
    url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
    body = {"model": EMBED_MODEL, "input": {"texts": texts},
            "parameters": {"text_type": "document"}}
    for attempt in range(3):
        try:
            r = client.post(url, json=body, timeout=60)
            r.raise_for_status()
            out = r.json()["output"]["embeddings"]
            out.sort(key=lambda e: e["text_index"])
            return [e["embedding"] for e in out]
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            wait = 2 ** attempt * 2
            print(f"  embedding 重试 {attempt + 1}: {e}，{wait}s 后重试")
            time.sleep(wait)
    return []  # unreachable


def sb_client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{SUPABASE_URL}/rest/v1",
        headers={"apikey": SUPABASE_KEY,
                 "Authorization": f"Bearer {SUPABASE_KEY}",
                 "Content-Type": "application/json",
                 "Prefer": "return=minimal"},
        timeout=60,
    )


def wipe(sb: httpx.Client):
    """全量重灌：先清 chunks 再清 documents。"""
    r = sb.delete("/chunks", params={"id": "gte.0"})
    r.raise_for_status()
    r = sb.delete("/documents", params={"slug": "neq."})
    r.raise_for_status()
    print("已清空旧数据")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不联网")
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
        for i, c in enumerate(chunk_text(d.pop("_content"))):
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
        wipe(sb)
        for i in range(0, len(docs), INSERT_BATCH):
            r = sb.post("/documents", json=[{k: v for k, v in d.items()} for d in docs[i:i + INSERT_BATCH]])
            r.raise_for_status()
        print(f"documents 写入 {len(docs)} 行")

        for i in range(0, len(all_chunks), EMBED_BATCH):
            batch = all_chunks[i:i + EMBED_BATCH]
            vecs = embed_batch(emb, [c["text"] for c in batch])
            for c, v in zip(batch, vecs):
                c["embedding"] = "[" + ",".join(f"{x:.6f}" for x in v) + "]"
            if (i // EMBED_BATCH) % 10 == 0:
                print(f"embedding {i}/{len(all_chunks)}")
        print("embedding 全部完成")

        for i in range(0, len(all_chunks), INSERT_BATCH):
            r = sb.post("/chunks", json=all_chunks[i:i + INSERT_BATCH])
            r.raise_for_status()
            if (i // INSERT_BATCH) % 5 == 0:
                print(f"chunks 写入 {i}/{len(all_chunks)}")
        print(f"完成：{len(all_chunks)} 个分段入库。")


if __name__ == "__main__":
    main()

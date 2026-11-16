"""知识库检索：Supabase RPC match_chunks（向量+关键词混合，见 ingest/schema.sql）。"""
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

try:
    from .kb_source import to_mirror_url
except ImportError:  # FC 以 backend 目录为根启动
    from kb_source import to_mirror_url

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
# 知识库镜像站地址：设置后来源链接一律指向镜像，不再依赖原始站点
KB_SOURCE_BASE = os.getenv("KB_SOURCE_BASE", "").rstrip("/")


def _to_mirror(row: dict) -> dict:
    if row.get("source_url"):
        row["source_url"] = to_mirror_url(row["source_url"], KB_SOURCE_BASE)
    return row

_client = httpx.Client(
    base_url=f"{SUPABASE_URL}/rest/v1",
    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    timeout=30,
)


def search(qvec: list[float], qtext: str, scope: str = "all", k: int = 8):
    """返回 [{doc_slug,title,seq,text,source_url,score}]；空向量时退化为关键词。"""
    if qvec:
        r = _client.post("/rpc/match_chunks", json={
            "qvec": "[" + ",".join(f"{x:.6f}" for x in qvec) + "]",
            "qtext": qtext, "scope": scope, "k": k})
        r.raise_for_status()
        return [_to_mirror(h) for h in r.json()]
    # 无向量兜底：纯关键词 ILIKE（embedding 服务挂了也能跑）
    r = _client.get("/chunks", params={
        "select": "doc_slug,seq,text,source_url",
        "text": f"ilike.*{qtext}*", "visibility": "eq.public",
        "status": "eq.ready", "limit": k,
        **({"kb_scope": f"eq.{scope}"} if scope != "all" else {}),
    })
    r.raise_for_status()
    return [_to_mirror(h) for h in r.json()]

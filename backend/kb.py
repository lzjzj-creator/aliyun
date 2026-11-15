"""知识库检索：Supabase RPC match_chunks（向量+关键词混合，见 ingest/schema.sql）。"""
import os
import httpx

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

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
        return r.json()
    # 无向量兜底：纯关键词 ILIKE（embedding 服务挂了也能跑）
    r = _client.get("/chunks", params={
        "select": "doc_slug,seq,text,source_url",
        "text": f"ilike.*{qtext}*", "visibility": "eq.public",
        "status": "eq.ready", "limit": k,
        **({"kb_scope": f"eq.{scope}"} if scope != "all" else {}),
    })
    r.raise_for_status()
    return r.json()

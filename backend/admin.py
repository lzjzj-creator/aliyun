"""管理员入口：密码校验、无状态会话令牌、知识库概览与检索。"""
import os
import re
import time
from collections import Counter

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

try:
    from .admin_auth import (SESSION_TTL, admin_ready, issue_session,
                             verify_password, verify_session)
except ImportError:  # FC 以 backend 目录为根启动
    from admin_auth import (SESSION_TTL, admin_ready, issue_session,
                            verify_password, verify_session)

router = APIRouter()

MAX_FAILS = 5
FAIL_WINDOW = 300
_fails: dict[str, tuple[int, float]] = {}


def _client_key(request: Request) -> str:
    # FC 网关在函数之前，client.host 是网关 IP：取 XFF 链尾（网关追加的真实来源），
    # 否则所有访客共用一个限流桶，任意人输错 5 次会把管理员也锁在门外。
    xff = request.headers.get("x-forwarded-for", "")
    if xff.strip():
        return xff.split(",")[-1].strip()
    return (request.client.host if request.client else "") or "unknown"


def _check_throttle(key: str) -> None:
    count, first = _fails.get(key, (0, 0.0))
    if count and time.monotonic() - first > FAIL_WINDOW:
        _fails.pop(key, None)
        return
    if count >= MAX_FAILS:
        raise HTTPException(status_code=429, detail="尝试次数过多，请 5 分钟后再试")


def _record_fail(key: str) -> None:
    now = time.monotonic()
    count, first = _fails.get(key, (0, now))
    if now - first > FAIL_WINDOW:
        count, first = 0, now
    _fails[key] = (count + 1, first)
    if len(_fails) > 1000:
        for k, (_, started) in list(_fails.items()):
            if now - started > FAIL_WINDOW:
                _fails.pop(k, None)


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not admin_ready():
        raise HTTPException(status_code=503, detail="管理员入口尚未配置")
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not verify_session(token):
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")


class AdminLoginReq(BaseModel):
    password: str = Field(min_length=1, max_length=256)


try:
    from . import memory as _memory
except ImportError:  # FC 以 backend 目录为根启动
    import memory as _memory


class CleanupReq(BaseModel):
    # strict：物理删除的开关不能被 "false"/"0" 这类字符串悄悄带过去
    model_config = ConfigDict(strict=True, extra="forbid")
    days: int = Field(default=90, ge=90, le=3650)
    batch_size: int = Field(default=500, ge=1, le=10000)
    dry_run: bool = True


def _message_prune_report(req: CleanupReq):
    """v2_prune_expired_messages RPC may not be deployed yet — report instead
    of failing the whole cleanup check."""
    try:
        return _memory.prune_expired_messages(req.batch_size, req.dry_run)
    except _memory.MemoryStoreError:
        return {"pending_migration": True}


@router.post("/api/admin/cleanup")
def admin_cleanup(req: CleanupReq, _: None = Depends(require_admin)):
    """保留期核对：默认只报命中行数，物理删除要显式 dry_run=false。

    定时任务由数据库侧 pg_cron（或运维批准的定时器）承担，这里只是首次核对与
    事后验证入口；不在任何请求路径里顺手做物理清理。响应只有计数，不含会话内容。
    """
    try:
        report = {"days": req.days, "dry_run": req.dry_run,
                  "expired_sessions": _memory.cleanup_expired(req.days, req.dry_run),
                  "expired_messages": _message_prune_report(req),
                  "quota": _memory.prune_quota(req.dry_run)}
    except ValueError:
        raise HTTPException(status_code=400, detail="保留期参数不合法") from None
    except _memory.MemoryStoreError:
        raise HTTPException(status_code=503, detail="配额存储暂时不可用") from None
    try:
        with _sb_client() as client:
            _audit(client, "cleanup", {"dry_run": req.dry_run, "report": report})
    except HTTPException:
        pass
    return {"ok": True, "data": report}


@router.post("/api/admin/login")
def admin_login(req: AdminLoginReq, request: Request):
    if not admin_ready():
        raise HTTPException(status_code=503, detail="管理员入口尚未配置")
    key = _client_key(request)
    _check_throttle(key)
    if not verify_password(req.password):
        _record_fail(key)
        raise HTTPException(status_code=401, detail="密码不正确")
    _fails.pop(key, None)
    try:
        with _sb_client() as client:
            _audit(client, "admin_login", {})
    except HTTPException:
        pass
    return {"ok": True, "token": issue_session(), "expires_in": SESSION_TTL}


def _sb_client() -> httpx.Client:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise HTTPException(status_code=503, detail="知识库数据源尚未配置")
    return httpx.Client(
        base_url=f"{url}/rest/v1",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )


def _count(client: httpx.Client, table: str) -> int:
    r = client.get(f"/{table}", params={"select": "*", "limit": 1},
                   headers={"Prefer": "count=exact", "Range": "0-0"})
    r.raise_for_status()
    total = r.headers.get("content-range", "/0").rsplit("/", 1)[-1]
    return int(total) if total.isdigit() else 0


def _all_scopes(client: httpx.Client) -> Counter:
    counts, offset = Counter(), 0
    while True:
        r = client.get("/documents", params={
            "select": "kb_scope", "limit": 1000, "offset": offset})
        r.raise_for_status()
        rows = r.json()
        counts.update((row.get("kb_scope") or "meta") for row in rows)
        if len(rows) < 1000:
            return counts
        offset += 1000


@router.get("/api/admin/pdf-metrics")
def admin_pdf_metrics(_: None = Depends(require_admin)):
    try:
        from .resume_pdf import render_metrics
    except ImportError:
        from resume_pdf import render_metrics
    return {"ok": True, "data": render_metrics()}


@router.get("/api/admin/overview")
def admin_overview(_: None = Depends(require_admin)):
    with _sb_client() as client:
        try:
            documents = _count(client, "documents")
            chunks = _count(client, "chunks")
            by_scope = _all_scopes(client)
            r = client.get("/documents", params={
                "select": "slug,title,kb_scope,source_url,char_count,created_at",
                "order": "created_at.desc", "limit": 8,
            })
            r.raise_for_status()
            recent = r.json()
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="知识库数据源暂时不可用，请稍后重试")
    return {
        "ok": True,
        "data": {
            "documents": documents,
            "chunks": chunks,
            "by_scope": dict(by_scope),
            "recent": recent,
        },
    }


@router.get("/api/admin/documents")
def admin_documents(
    q: str = Query(default="", max_length=80),
    scope: str = Query(default="", max_length=30),
    _: None = Depends(require_admin),
):
    params = {
        "select": "slug,title,kb_scope,source_url,char_count",
        "order": "char_count.desc",
        "limit": 30,
    }
    q = q.strip()
    if q:
        # 过滤 PostgREST 过滤表达式保留字符，避免管理员输入破坏查询结构
        q = re.sub(r"[,()*%\"\\]", " ", q).strip()
        if q:
            params["title"] = f"ilike.*{q}*"
    if scope:
        if not re.fullmatch(r"[a-z_]{1,30}", scope):
            raise HTTPException(status_code=400, detail="范围参数不合法")
        params["kb_scope"] = f"eq.{scope}"
    with _sb_client() as client:
        try:
            r = client.get("/documents", params=params)
            r.raise_for_status()
            items = r.json()
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="知识库数据源暂时不可用，请稍后重试")
    return {"ok": True, "data": {"items": items}}


def _count_where(client: httpx.Client, table: str, params: dict) -> int | None:
    """Filtered count; None when the table is not deployed yet."""
    try:
        r = client.get(f"/{table}", params={**params, "limit": 1},
                       headers={"Prefer": "count=exact", "Range": "0-0"})
        r.raise_for_status()
        total = r.headers.get("content-range", "/0").rsplit("/", 1)[-1]
        return int(total) if total.isdigit() else 0
    except httpx.HTTPError:
        return None


def _audit(client: httpx.Client, action: str, detail: dict) -> None:
    """Admin action audit. Failure to record must not break the action."""
    try:
        client.post("/admin_audit", json={"action": action, "detail": detail})
    except httpx.HTTPError:
        pass


@router.get("/api/admin/users")
def admin_users(limit: int = Query(default=100, ge=1, le=500),
                _: None = Depends(require_admin)):
    """v5 账号列表（管理后台）：只给公共字段，绝不返回密码哈希。"""
    with _sb_client() as client:
        try:
            r = client.get("/app_users", params={
                "select": "id,kind,username,init_profile,guide_seen_at,"
                          "created_at,last_login_at",
                "deleted_at": "is.null",
                "order": "created_at.desc", "limit": limit})
            r.raise_for_status()
            rows = r.json()
        except httpx.HTTPError:
            raise HTTPException(status_code=502,
                                detail="账号数据源暂时不可用，请稍后重试")
    users = [{"id": u["id"], "kind": u["kind"], "username": u.get("username"),
              "init_done": bool((u.get("init_profile") or {}).get("_done")),
              "guide_seen": bool(u.get("guide_seen_at")),
              "created_at": u.get("created_at"),
              "last_login_at": u.get("last_login_at")} for u in rows]
    return {"ok": True, "data": {"users": users, "count": len(users)}}


@router.get("/api/admin/stats")
def admin_stats(_: None = Depends(require_admin)):
    """运营概览：账号/会话/消息规模 + v3 记忆规模 + 当日 token 用量。
    只给计数，不返回任何聊天正文或用户标识。"""
    with _sb_client() as client:
        data = {
            "users": {
                "total": _count_where(client, "app_users", {"select": "id"}),
                "registered": _count_where(client, "app_users", {
                    "select": "id", "kind": "eq.user"}),
            },
            "chat": {
                "sessions": _count_where(client, "chat_sessions", {"select": "id"}),
                "messages": _count_where(client, "chat_messages", {"select": "id"}),
            },
            "v3": {
                "owners": _count_where(client, "v3_owner", {"select": "id"}),
                "facts": _count_where(client, "v3_facts", {"select": "id"}),
                "resumes": _count_where(client, "v3_resume_documents", {"select": "id"}),
            },
        }
    try:
        data["tokens_today"] = _memory.token_usage_today()
    except _memory.MemoryStoreError:
        data["tokens_today"] = None
    return {"ok": True, "data": data}


@router.get("/api/admin/audit")
def admin_audit(limit: int = Query(default=50, ge=1, le=200),
                _: None = Depends(require_admin)):
    with _sb_client() as client:
        try:
            r = client.get("/admin_audit", params={
                "select": "id,action,detail,created_at",
                "order": "id.desc", "limit": limit})
            r.raise_for_status()
            items = r.json()
        except httpx.HTTPError:
            items = []
    return {"ok": True, "data": {"items": items}}
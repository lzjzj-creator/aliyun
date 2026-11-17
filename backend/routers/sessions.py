"""v2 sessions and transitional v1 pair persistence; no LLM/business logic."""
import json

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from .. import memory, session_security as security
except ImportError:  # FC starts with backend on sys.path
    import memory
    import session_security as security

router = APIRouter(prefix="/api/v2")


def _storage(call):
    try:
        return call()
    except memory.SessionNotFound:
        raise security.not_found() from None
    except memory.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None


class MessageReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # null 时由服务端建会话并一次性返回 session_token（fixture messages.json 契约）
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    client_msg_id: str = Field(min_length=1, max_length=128)
    user: str = Field(min_length=1, max_length=12000)
    assistant: str = Field(min_length=1, max_length=30000)
    meta: dict | None = None

    @field_validator("client_msg_id", "user", "assistant")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("meta")
    @classmethod
    def meta_bounded(cls, value):
        if value is not None and len(json.dumps(value, ensure_ascii=False)) > 4000:
            raise ValueError("meta too large")
        return value


def _owner(request, session_id, anon, token):
    security.enforce_ip(request)
    anon = security.require_anon(anon)
    return anon, security.require_owner(session_id, anon, token)


@router.get("/sessions")
def list_sessions(request: Request, x_anon_id: str | None = Header(default=None)):
    security.enforce_ip(request)
    anon = security.require_anon(x_anon_id)
    rows = _storage(lambda: memory.list_sessions(anon))
    fields = {"id", "title", "stage", "created_at", "updated_at", "last_message_at"}
    return {"items": [{k: v for k, v in row.items() if k in fields} for row in rows]}


@router.get("/sessions/{session_id}")
def get_session(request: Request, session_id: str,
                x_anon_id: str | None = Header(default=None),
                x_session_token: str | None = Header(default=None)):
    _, sid = _owner(request, session_id, x_anon_id, x_session_token)
    bundle = _storage(lambda: memory.get_session_bundle(sid))
    if bundle is None:
        raise security.not_found()
    return bundle


@router.delete("/sessions/{session_id}")
def delete_session(request: Request, session_id: str,
                   x_anon_id: str | None = Header(default=None),
                   x_session_token: str | None = Header(default=None)):
    _, sid = _owner(request, session_id, x_anon_id, x_session_token)
    # Use the same per-session lease as writers, avoiding deletion mid-pair.
    with security.limiter.stream(sid):
        if not _storage(lambda: memory.delete_session(sid)):
            raise security.not_found()
    return {"ok": True}


def _write_pair(sid, req):
    user = _storage(lambda: memory.append_message(sid, "user", req.user,
                                                  client_msg_id=req.client_msg_id))
    assistant = _storage(lambda: memory.append_message(sid, "assistant", req.assistant,
                                                       client_msg_id=req.client_msg_id,
                                                       meta=req.meta))
    return {"session_id": sid, "message_ids": [user["id"], assistant["id"]]}


@router.post("/messages")
def append_legacy_message(req: MessageReq, request: Request,
                         x_anon_id: str | None = Header(default=None),
                         x_session_token: str | None = Header(default=None)):
    security.enforce_ip(request)
    anon = security.require_anon(x_anon_id)
    if req.session_id is None:
        # First round: no session exists yet, so no token/ownership check. The
        # token is returned exactly once; clients must reuse session_id on retry
        # (a retried null session_id creates a new session by design).
        security.charge_turn(anon)
        created = _storage(lambda: memory.create_session(anon))
        result = _write_pair(created["session"]["id"], req)
        return {**result, "session_token": created["token"]}
    sid = security.require_owner(req.session_id, anon, x_session_token)
    # All pair checks and writes are inside the lease. A concurrent request gets
    # 429 and can retry; no stale pre-lock snapshot or double assistant insert.
    with security.limiter.stream(sid):
        bundle = _storage(lambda: memory.get_session_bundle(sid))
        if bundle is None:
            raise security.not_found()
        user = next((m for m in bundle["messages"] if m.get("role") == "user"
                     and m.get("client_msg_id") == req.client_msg_id), None)
        assistant = next((m for m in bundle["messages"] if m.get("role") == "assistant"
                          and m.get("client_msg_id") == req.client_msg_id), None)
        if (user and user["content"] != req.user) or (assistant and assistant["content"] != req.assistant):
            raise HTTPException(409, "幂等键已用于其他内容")
        if assistant and not user:
            raise HTTPException(409, "消息状态冲突")
        if user is None:
            security.charge_turn(anon)
            user = _storage(lambda: memory.append_message(sid, "user", req.user,
                                                          client_msg_id=req.client_msg_id))
        # If assistant persistence previously failed, reuse the persisted user
        # and complete only the missing half, without charging a second turn.
        if assistant is None:
            assistant = _storage(lambda: memory.append_message(sid, "assistant", req.assistant,
                                                               client_msg_id=req.client_msg_id,
                                                               meta=req.meta))
        return {"session_id": sid, "message_ids": [user["id"], assistant["id"]]}

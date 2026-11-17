"""v2 sessions and transitional v1 pair persistence; no LLM/business logic."""
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
    session_id: str = Field(min_length=1, max_length=64)
    client_msg_id: str = Field(min_length=1, max_length=128)
    user: str = Field(min_length=1, max_length=12000)
    assistant: str = Field(min_length=1, max_length=30000)

    @field_validator("client_msg_id", "user", "assistant")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
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


@router.post("/messages")
def append_legacy_message(req: MessageReq, request: Request,
                         x_anon_id: str | None = Header(default=None),
                         x_session_token: str | None = Header(default=None)):
    anon, sid = _owner(request, req.session_id, x_anon_id, x_session_token)
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
                                                               client_msg_id=req.client_msg_id))
        return {"user": user, "assistant": assistant}

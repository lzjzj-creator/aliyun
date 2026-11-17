"""v2 访谈 SSE 端点：POST /api/v2/chat/stream（§4 事件协议）。

- session_id=null → 服务端建会话，session 事件一次性带回 session_token
- 每轮：verify owner → 幂等查重 → append user → 流式产出 → 收尾落 assistant + stage
- ("stage", x) 为 coach 内部信号，仅写库不透出；done 带最终 stage/coverage
"""
import json
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from .. import coach, memory, session_security as security
except ImportError:  # FC starts with backend on sys.path
    import coach
    import memory
    import session_security as security

router = APIRouter(prefix="/api/v2")
PROTOCOL = 2
MAX_STEPS = 20


class ChatReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(default=None, max_length=64)
    client_msg_id: str | None = Field(default=None, max_length=128)
    message: str = Field(default="", max_length=2000)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value):
        return (value or "").strip()


def _sse(event, data):
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _storage(call):
    try:
        return call()
    except memory.SessionNotFound:
        raise security.not_found() from None
    except memory.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None


def _err(code, message, retryable=False):
    return {"code": code, "message": message, "retryable": retryable}


@router.post("/chat/stream")
def chat_stream(req: ChatReq, request: Request,
                x_anon_id: str | None = Header(default=None),
                x_session_token: str | None = Header(default=None)):
    if os.getenv("COACH_ENABLED", "0") != "1":
        raise HTTPException(503, "访谈引擎暂未开放")
    security.enforce_ip(request)
    anon = security.require_anon(x_anon_id)

    is_new = req.session_id is None
    lease = None
    if is_new:
        security.charge_turn(anon)   # 与 /messages 建会话同口径，防刷会话
        created = _storage(lambda: memory.create_session(anon))
        session, token, sid = created["session"], created["token"], created["session"]["id"]
    else:
        sid = security.require_owner(req.session_id, anon, x_session_token)
        # 租约在响应前获取：竞争 → 429+Retry-After（§5/§10），查重/落库全在租约内
        lease = security.limiter.stream(sid)
        lease.__enter__()
        session = None
        token = None

    try:
        bundle = _storage(lambda: memory.get_session_bundle(sid))
        session = bundle["session"]
        profile_row = bundle.get("profile") or {}
        messages = bundle.get("messages") or []
        text = req.message

        dup = None
        if text:
            if req.client_msg_id:
                dup = next((m for m in messages if m.get("role") == "user"
                            and m.get("client_msg_id") == req.client_msg_id), None)
            if dup is None:
                security.charge_turn(anon)
                _storage(lambda: memory.append_message(
                    sid, "user", text, client_msg_id=req.client_msg_id))
                messages.append({"role": "user", "content": text})
                if is_new or session.get("title") == "新会话":
                    try:
                        memory.touch_title(sid, text)
                    except memory.MemoryStoreError:
                        pass
        elif not is_new:
            raise HTTPException(422, "message 不能为空")
    except Exception:
        if lease is not None:
            lease.__exit__(None, None, None)
        raise

    def gen():
        yield _sse("session", {
            "protocol": PROTOCOL, "session_id": sid,
            "stage": session.get("stage", "greeting"), "is_new": is_new,
            **({"session_token": token} if token else {}),
        })
        stage = session.get("stage", "greeting")
        steps, tokens, sources, cards, open_steps = [], [], [], [], {}
        reasoning = None
        coverage = (profile_row or {}).get("coverage")
        done_id = None

        try:
            if dup is not None:
                yield _sse("error", _err("DUPLICATE", "该消息已提交，请勿重复发送"))
                last_asst = next((m for m in reversed(messages)
                                  if m.get("role") == "assistant"), None)
                yield _sse("done", {"session_id": sid,
                                    "message_id": (last_asst or {}).get("id"),
                                    "stage": stage, "coverage": coverage})
                return

            asst = None
            try:
                if not text:
                    events = coach.greeting_events()
                else:
                    asst = _storage(lambda: memory.append_message(
                        sid, "assistant", "", status="streaming"))
                    done_id = asst["id"]
                    events = coach.turn_events(session, profile_row, messages, text)

                for ev, data in events:
                    if ev == "stage":          # 内部信号：不落事件流，逐次持久化
                        stage = data
                        try:
                            memory.set_session_stage(sid, stage)
                        except memory.MemoryStoreError:
                            pass
                        continue
                    if ev == "step_start":
                        if len(steps) >= MAX_STEPS:
                            continue           # 超上限既不下发也不落库
                        open_steps[data["id"]] = dict(data)
                        steps.append(open_steps[data["id"]])
                    elif ev == "step_end":
                        st = open_steps.get(data["id"])
                        if st is None:
                            continue
                        st.update(data)
                    elif ev == "token":
                        tokens.append(data)
                    elif ev == "sources":
                        sources.extend(data if isinstance(data, list) else [data])
                    elif ev == "card":
                        cards.append(data)
                    elif ev == "reasoning_summary":
                        reasoning = data       # 摘要留进 meta，供会话恢复时回放
                    elif ev == "profile" and isinstance(data, dict):
                        coverage = data.get("coverage", coverage)
                    yield _sse(ev, data)

                content = "".join(tokens)
                meta = {}
                if cards:
                    meta["cards"] = cards
                if reasoning:
                    meta["reasoning"] = reasoning
                meta = meta or None
                if asst is None:  # greeting：一次性写完成的招呼消息
                    asst = _storage(lambda: memory.append_message(
                        sid, "assistant", content, status="done", meta=meta))
                    done_id = asst["id"]
                else:
                    _storage(lambda: memory.update_message_status(
                        done_id, "done", content=content, steps=steps,
                        sources=sources, meta=meta))
                yield _sse("done", {"session_id": sid, "message_id": done_id,
                                    "stage": stage, "coverage": coverage})
            except memory.MemoryStoreError:
                if done_id is not None:
                    try:
                        memory.update_message_status(done_id, "error")
                    except memory.MemoryStoreError:
                        pass
                yield _sse("error", _err("STORAGE", "存储暂时不可用", retryable=True))
            except HTTPException as he:
                yield _sse("error", _err("HTTP", str(he.detail)[:120], retryable=True))
            except Exception:
                if done_id is not None:
                    try:
                        memory.update_message_status(done_id, "error")
                    except memory.MemoryStoreError:
                        pass
                yield _sse("error", _err("UPSTREAM", "模型服务暂时不可用，请重试", retryable=True))
            except BaseException:  # GeneratorExit/取消：assistant 不能永挂 streaming
                if done_id is not None:
                    try:
                        memory.update_message_status(done_id, "error")
                    except Exception:
                        pass
                raise
        finally:
            if lease is not None:
                lease.__exit__(None, None, None)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
    # 统一 JSON 编码：裸字符串里的 \n\n 会截断 SSE 帧丢数据（§4 协议安全）
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _storage(call):
    try:
        return call()
    except memory.SessionNotFound:
        raise security.not_found() from None
    except memory.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None


def _err(code, message, retryable=False):
    return {"code": code, "message": message, "retryable": retryable}


def _seed_profile(auth_user: dict) -> dict | None:
    """v5: translate app_users.init_profile into a user_profiles.profile seed.

    Profile paths follow coach._slot_filled's fallback reads (identity.*,
    basics.target_role) plus the extractor's goal.target_role so both readers
    see the initialized values. material_notes ride along so the interviewer
    can use them as evidence hints without re-asking.
    """
    init = (auth_user or {}).get("init_profile") or {}
    if not isinstance(init, dict) or not init:
        return None
    profile = {}
    identity = dict(init.get("identity") or {})
    edu = init.get("education")
    if isinstance(edu, list) and edu and isinstance(edu[0], dict):
        for key in ("school", "major", "degree", "period", "gpa_or_rank"):
            if edu[0].get(key):
                identity.setdefault(key, edu[0][key])
    basics = init.get("basics") or {}
    for key in ("name", "phone", "email", "city"):
        if basics.get(key):
            identity.setdefault(key, basics[key])
    if identity:
        profile["identity"] = {k: v for k, v in identity.items()
                               if isinstance(v, str) and v.strip()}
    if isinstance(basics.get("target_role"), str) and basics["target_role"].strip():
        profile["goal"] = {"target_role": basics["target_role"].strip()}
        profile["basics"] = {"target_role": basics["target_role"].strip()}
    notes = init.get("material_notes")
    if isinstance(notes, dict):
        profile["material_notes"] = {k: v for k, v in notes.items()
                                     if isinstance(v, str) and v.strip()}
    return profile or None


def ensure_seed(auth_user: dict | None, bundle: dict | None) -> dict | None:
    """v5: backfill init_profile into an existing session's profile.

    Sessions created before init (guest phase, or before onboarding) never
    got the create-time seed. On Bearer access, merge missing seed paths and
    persist with recomputed coverage. Session values always win — interview
    corrections are never overwritten by the seed."""
    if not auth_user or not bundle or not bundle.get("session"):
        return bundle
    seed = _seed_profile(auth_user)
    if not seed:
        return bundle
    row = bundle.get("profile") or {}
    profile = row.get("profile") or {}
    # 两层深合并：seed 只补缺，会话已有值（含访谈修正）一律优先
    merged = dict(profile)
    changed = False
    for key, val in seed.items():
        cur = merged.get(key)
        if isinstance(val, dict) and isinstance(cur, dict):
            fill = {k: v for k, v in val.items() if not cur.get(k)}
            if fill:
                merged[key] = {**cur, **fill}
                changed = True
        elif not cur:
            merged[key] = val
            changed = True
    if not changed:
        return bundle
    detail = row.get("coverage_detail") or {}
    try:
        updated = memory.merge_profile(
            bundle["session"]["id"], merged, int(row.get("version") or 0),
            coverage=coach.coverage_of(merged, detail)[0],
            coverage_detail=detail)
    except (memory.ProfileConflict, memory.SessionNotFound,
            memory.MemoryStoreError):
        return bundle          # 并发冲突/存储故障：best-effort，下一轮再补
    bundle["profile"] = updated
    return bundle


class _LeaseResponse(StreamingResponse):
    """Release even if ASGI send fails before the sync iterator is first advanced."""
    def __init__(self, *args, release, **kwargs):
        self._release = release
        self._iterator = args[0]
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        from starlette.requests import ClientDisconnect
        try:
            await super().__call__(scope, receive, send)
        except BaseException as exc:
            # anyio task groups may wrap the send failure in an ExceptionGroup;
            # either shape means the client went away mid-write.
            leaves = getattr(exc, "exceptions", (exc,))
            if any(isinstance(e, OSError) for e in leaves):
                raise ClientDisconnect() from None
            raise
        finally:
            try:
                self._iterator.close()
            finally:
                self._release()


def _retry_result(messages, user, text, key):
    if user.get("content") != text:
        raise HTTPException(409, "client_msg_id 已用于不同消息")
    results = [m for m in messages if m.get("role") == "assistant"
               and m.get("client_msg_id") == key]
    if results:
        result = results[-1]
        if result.get("status") not in {"done", "error", "streaming"}:
            raise HTTPException(409, "该消息结果状态不明确，请刷新会话")
        return result
    # Legacy assistants have no provable request association. Never pick the last
    # assistant (which may belong to a different turn), or regenerate over one.
    index = messages.index(user)
    if any(m.get("role") == "assistant" and not m.get("client_msg_id")
           for m in messages[index + 1:]):
        raise HTTPException(409, "历史消息缺少请求关联，无法安全重试，请刷新会话")
    return None


@router.post("/chat/stream")
def chat_stream(req: ChatReq, request: Request,
                x_anon_id: str | None = Header(default=None),
                x_session_token: str | None = Header(default=None),
                authorization: str | None = Header(default=None)):
    if os.getenv("COACH_ENABLED", "0") != "1":
        raise HTTPException(503, "访谈引擎暂未开放")
    security.enforce_ip(request)
    auth_user, anon = security.require_identity(request, authorization, x_anon_id)
    if not coach.budget_ok():      # §10 日 token 预算触顶 → 停新访谈
        raise HTTPException(503, "今日访谈额度已用完，请明天再来")

    is_new = req.session_id is None
    lease = None
    if is_new:
        security.charge_turn(anon)   # 建会话+首轮计一次配额（与 /messages 同口径）
        # v5: init_profile seeds the new session's profile so initialized
        # users are never re-asked grade/school/major/target_role.
        seed = _seed_profile(auth_user) if auth_user else None
        created = _storage(lambda: memory.create_session(
            anon, user_id=auth_user["id"] if auth_user else None,
            seed_profile=seed))
        session, token, sid = created["session"], created["token"], created["session"]["id"]
    else:
        sid, _ = security.require_access(
            request, req.session_id, authorization, anon, x_session_token)
        # 租约在响应前获取：竞争 → 429+Retry-After（§5/§10），查重/落库全在租约内
        lease = security.limiter.stream(sid)
        lease.__enter__()
        session = None
        token = None

    def release():
        nonlocal lease
        held, lease = lease, None
        if held is not None:
            held.__exit__(None, None, None)

    def compensate():
        if is_new:
            try:
                memory.delete_session(sid)
            except memory.MemoryStoreError:
                pass

    try:
        if is_new:
            lease = security.limiter.stream(sid)
            lease.__enter__()
        bundle = _storage(lambda: memory.get_session_bundle(sid))
        if bundle is None or bundle.get("session") is None:
            raise security.not_found()     # verify→删除竞态下统一 404（同 sessions.py）
        # v5: 初始化之前建的老会话按 Bearer 身份回填种子画像
        if not is_new:
            bundle = ensure_seed(auth_user, bundle)
        session = bundle["session"]
        profile_row = bundle.get("profile") or {}
        messages = bundle.get("messages") or []
        text = req.message

        dup = None
        result = None
        if req.client_msg_id:
            dup = next((m for m in messages if m.get("role") == "user"
                        and m.get("client_msg_id") == req.client_msg_id), None)
        if dup is not None:
            result = _retry_result(messages, dup, text, req.client_msg_id)
        if text:
            if dup is None:
                if not is_new:          # is_new 已在建会话处计费，首轮不重复扣
                    security.charge_turn(anon)
                _storage(lambda: memory.append_message(
                    sid, "user", text, client_msg_id=req.client_msg_id))
                messages.append({"role": "user", "content": text})
                # 首轮标题由 v2_message_activity 触发器原子写入，无需 touch_title
        elif not is_new:
            raise HTTPException(422, "message 不能为空")
    except BaseException:
        release()
        compensate()
        raise

    def gen():
        stage = session.get("stage", "greeting")
        steps, tokens, sources, cards, open_steps = [], [], [], [], {}
        reasoning = None
        coverage = (profile_row or {}).get("coverage")
        done_id = None

        complete = False
        events = None

        def metadata():
            return {"cards": cards, "reasoning": reasoning,
                    "stage": stage, "coverage": coverage}

        try:
            yield _sse("session", {
                "protocol": PROTOCOL, "session_id": sid,
                "stage": stage, "is_new": is_new,
                **({"session_token": token} if token else {}),
            })
            if result is not None and result.get("status") == "streaming":
                yield _sse("error", _err("IN_PROGRESS", "该消息仍在生成中，请稍后重试", True))
                return
            if result is not None and result.get("status") == "done":
                meta = result.get("meta") or {}
                for step in result.get("steps") or []:
                    yield _sse("step_start", step)
                    yield _sse("step_end", step)
                if meta.get("reasoning"):
                    yield _sse("reasoning_summary", meta["reasoning"])
                if result.get("sources"):
                    yield _sse("sources", result["sources"])
                yield _sse("token", result.get("content") or "")
                for card in meta.get("cards") or []:
                    yield _sse("card", card)
                yield _sse("done", {"session_id": sid, "message_id": result["id"],
                                    "stage": meta.get("stage", stage),
                                    "coverage": meta.get("coverage", coverage)})
                return

            try:
                asst = _storage(lambda: memory.append_message(
                    sid, "assistant", "", status="streaming",
                    client_msg_id=req.client_msg_id))
                done_id = asst["id"]
                if not text:
                    events = coach.greeting_events()
                else:
                    # Failed attempts are retained for audit/recovery, not supplied
                    # as successful assistant history to the next model attempt.
                    history = [m for m in messages if m.get("role") != "assistant"
                               or m.get("status", "done") == "done"]
                    events = coach.turn_events(session, profile_row, history, text)

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
                _storage(lambda: memory.update_message_status(
                    done_id, "done", content=content, steps=steps,
                    sources=sources, meta=metadata()))
                complete = True
                yield _sse("done", {"session_id": sid, "message_id": done_id,
                                    "stage": stage, "coverage": coverage})
            except memory.MemoryStoreError:
                yield _sse("error", _err("STORAGE", "存储暂时不可用", retryable=True))
            except HTTPException as he:
                yield _sse("error", _err("HTTP", str(he.detail)[:120],
                                         retryable=he.status_code == 429
                                         or he.status_code >= 500))
            except Exception:
                yield _sse("error", _err("UPSTREAM", "模型服务暂时不可用，请重试", retryable=True))
        finally:
            try:
                if done_id is not None and not complete:
                    try:
                        memory.update_message_status(
                            done_id, "error", content="".join(tokens),
                            steps=steps, sources=sources, meta=metadata())
                    except memory.MemoryStoreError:
                        pass
                if is_new and not tokens and not complete:
                    compensate()
            finally:
                try:
                    if events is not None and hasattr(events, "close"):
                        events.close()
                finally:
                    release()

    return _LeaseResponse(
        gen(), release=release, media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

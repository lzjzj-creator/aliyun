"""v4 account + data-asset routes (/api/v4/*), guarded by V4_ACCOUNTS_ENABLED.

Bearer token (guest or registered user) is the only credential. Registering
upgrades the guest row in place so every user_id-keyed record follows the
account. Username/password auth only; no recovery channel by design.
"""
import re
import time

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from .. import accounts_v4
    from .. import session_security as security
    from .. import session_security_v4 as security_v4
except ImportError:  # FC starts with backend on sys.path
    import accounts_v4
    import session_security as security
    import session_security_v4 as security_v4

router = APIRouter(prefix="/api/v4")
PROTOCOL = 4

USERNAME_RE = re.compile(r"^[\w一-龥-]{2,24}$")
# 登录：每分钟尝试数 + 连续失败指数退避（v5 §3.3）。
# in-process 足够当前用户规模；register 用尝试速率档，login 失败才升级退避。
LOGIN_PER_MIN = 5
LOGIN_BACKOFF_AFTER = 5          # 第 N 次连续失败起退避
LOGIN_BACKOFF_MAX = 900          # 单次封顶 15 分钟
_login_hits: dict[str, list] = {}
_login_fails: dict[str, tuple] = {}   # username_lc -> (consecutive_fails, blocked_until)


def _login_limit(username_lc: str):
    """尝试前检查：被退避封锁 → 429+Retry-After；否则记一次尝试。"""
    now = time.monotonic()
    fails, blocked_until = _login_fails.get(username_lc, (0, 0.0))
    if blocked_until > now:
        raise security.throttled(blocked_until - now)
    hits = [t for t in _login_hits.get(username_lc, []) if now - t < 60]
    if len(hits) >= LOGIN_PER_MIN:
        raise security.throttled(int(60 - (now - hits[0])))
    hits.append(now)
    _login_hits[username_lc] = hits[-LOGIN_PER_MIN:]


def login_failed(username_lc: str):
    """凭证校验失败后调用：连续失败 ≥5 次起指数退避（30s→60s→120s…封顶 15min）。"""
    fails, _ = _login_fails.get(username_lc, (0, 0.0))
    fails += 1
    blocked_until = 0.0
    if fails > LOGIN_BACKOFF_AFTER:
        blocked_until = time.monotonic() + min(
            30 * (2 ** (fails - LOGIN_BACKOFF_AFTER)), LOGIN_BACKOFF_MAX)
    _login_fails[username_lc] = (fails, blocked_until)


def login_succeeded(username_lc: str):
    _login_fails.pop(username_lc, None)


def _err(status: int, code: str, message: str, retryable: bool = False):
    """v5 §3.3 统一错误形状：{code, message, retryable}。"""
    return HTTPException(status, detail={
        "code": code, "message": message, "retryable": retryable})


def _storage(call):
    try:
        return call()
    except accounts_v4.MemoryStoreError:
        raise HTTPException(503, detail={
            "code": "STORE_UNAVAILABLE", "message": "存储暂时不可用",
            "retryable": True}) from None
    except ValueError:
        raise HTTPException(422, detail={
            "code": "INVALID_INPUT", "message": "请求参数无效",
            "retryable": False}) from None


def _public_user(row) -> dict:
    return {"id": row["id"], "kind": row["kind"],
            "username": row.get("username"),
            "init_done": bool((row.get("init_profile") or {}).get("_done")),
            "guide_seen": bool(row.get("guide_seen_at")),
            "guide_seen_at": row.get("guide_seen_at")}


class RegisterReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=security_v4.PASSWORD_MAX)


class LoginReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=security_v4.PASSWORD_MAX)


class DeleteReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str

    @field_validator("confirm")
    @classmethod
    def exact_phrase(cls, value):
        if value != "DELETE_ACCOUNT":
            raise ValueError("confirm must be DELETE_ACCOUNT")
        return value


class SettingsReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: dict = Field(default_factory=dict)


class InitReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    init_profile: dict = Field(default_factory=dict)


class DraftReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_version: str = Field(default="", max_length=32)
    title: str = Field(default="未命名草稿", max_length=80)
    fields: dict = Field(default_factory=dict)
    evidence_map: dict = Field(default_factory=dict)
    gaps: list = Field(default_factory=list)
    # 前端发 data-url 字符串（与 fields._meta.photo 同形状）；dict 兼容旧客户端
    photo: dict | str | None = None
    draft_id: str | None = Field(default=None, max_length=64)


# --- auth ----------------------------------------------------------------------

@router.post("/auth/guest")
def create_guest(request: Request):
    """Silent guest bootstrap; idempotent in the sense that it is always safe
    to call when the client holds no token. Each call mints a fresh token."""
    security.enforce_ip(request)
    user = _storage(accounts_v4.create_guest)
    token = security_v4.issue_auth(user["id"])
    return {"protocol": PROTOCOL, "auth_token": token,
            "user": _public_user(user)}


@router.post("/auth/register")
def register(request: Request, req: RegisterReq,
             authorization: str | None = Header(default=None)):
    security.enforce_ip(request)
    username = req.username.strip()
    if not USERNAME_RE.match(username):
        raise _err(422, "INVALID_USERNAME", "用户名需为 2–24 位中英文、数字或 -_")
    if len(req.password) < security_v4.PASSWORD_MIN:
        raise _err(422, "WEAK_PASSWORD", "密码至少 8 位")
    username_lc = username.lower()
    _login_limit(username_lc)
    # Registering upgrades the *current* guest row so its data follows along.
    # Without a guest token the caller still gets an account, just a fresh one.
    guest = security_v4.optional_user(request, authorization)
    taken = _storage(lambda: accounts_v4.username_taken(username_lc))
    if taken:
        raise _err(409, "USERNAME_TAKEN", "该用户名已被注册")
    pw_hash = security_v4.hash_password(req.password)
    if guest is not None and guest.get("kind") == "guest":
        user = _storage(lambda: accounts_v4.upgrade_to_user(
            guest["id"], username, username_lc, pw_hash))
        # Keep existing tokens valid; return the same bearer credential.
        token_hash = security_v4.bearer_token_hash(request, authorization)
        token = None
        if token_hash:
            scheme, _, raw = (authorization or "").partition(" ")
            token = raw.strip()
        if not token:
            token = security_v4.issue_auth(user["id"])
    else:
        user = _storage(lambda: accounts_v4.upgrade_to_user(
            _storage(accounts_v4.create_guest)["id"],
            username, username_lc, pw_hash))
        token = security_v4.issue_auth(user["id"])
    return {"protocol": PROTOCOL, "auth_token": token,
            "user": {**_public_user(user), "needs_init": True}}


@router.post("/auth/login")
def login(request: Request, req: LoginReq):
    security.enforce_ip(request)
    username_lc = req.username.strip().lower()
    _login_limit(username_lc)
    row = _storage(lambda: accounts_v4.get_user_by_username_lc(username_lc))
    # Uniform failure text: no oracle on whether the username exists.
    if row is None or not security_v4.verify_password(
            req.password, row.get("password_hash")):
        login_failed(username_lc)      # 连续失败 → 指数退避
        raise _err(401, "BAD_CREDENTIALS", "用户名或密码不正确")
    login_succeeded(username_lc)
    accounts_v4.touch_login(row["id"])
    token = security_v4.issue_auth(row["id"])
    return {"protocol": PROTOCOL, "auth_token": token,
            "user": _public_user(row)}


@router.post("/auth/logout")
def logout(request: Request,
           authorization: str | None = Header(default=None)):
    token_hash = security_v4.bearer_token_hash(request, authorization)
    if token_hash:
        _storage(lambda: accounts_v4.revoke_token(token_hash))
    return {"protocol": PROTOCOL, "ok": True}


# --- me --------------------------------------------------------------------------

def _me(request: Request, authorization: str | None):
    return security_v4.require_user(request, authorization)


@router.get("/me")
def get_me(request: Request,
           authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    counts = _storage(lambda: accounts_v4.count_assets(user["id"]))
    return {"protocol": PROTOCOL, **_public_user(user), "counts": counts,
            "init_profile": user.get("init_profile") or {},
            "settings": user.get("settings") or {}}


REQUIRED_INIT = [
    ("identity.grade", "年级"),
    ("basics.name", "姓名"),
    ("basics.phone", "电话"),
    ("basics.email", "邮箱"),
    ("basics.target_role", "求职意向"),
    ("education.0.school", "学校"),
    ("education.0.major", "专业"),
    ("education.0.degree", "学历"),
    ("education.0.period", "就读时间"),
]


def _get_path(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def validate_init_profile(profile: dict) -> list:
    """Required-field check per the resume_template contract's required marks."""
    issues = []
    for path, label in REQUIRED_INIT:
        value = _get_path(profile, path)
        if not isinstance(value, str) or not value.strip():
            issues.append({"path": path, "label": label, "code": "required"})
    email = _get_path(profile, "basics.email")
    if isinstance(email, str) and email.strip() and "@" not in email:
        issues.append({"path": "basics.email", "label": "邮箱",
                       "code": "format"})
    return issues


@router.put("/me/init")
def put_init(request: Request, req: InitReq,
             authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    issues = validate_init_profile(req.init_profile)
    if issues:
        raise HTTPException(422, detail={
            "code": "INIT_INCOMPLETE",
            "message": "必填项未填写完整",
            "retryable": False,
            "issues": issues})
    profile = dict(req.init_profile)
    profile["_done"] = True
    row = _storage(lambda: accounts_v4.save_init_profile(user["id"], profile))
    return {"protocol": PROTOCOL, "init_done": True,
            "init_profile": row.get("init_profile")}


@router.put("/me/settings")
def put_settings(request: Request, req: SettingsReq,
                 authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    import json as _json_mod
    if len(_json_mod.dumps(req.settings, ensure_ascii=False)) > 8000:
        raise _err(422, "SETTINGS_TOO_LARGE", "设置内容过大")
    row = _storage(lambda: accounts_v4.save_settings(user["id"], req.settings))
    return {"protocol": PROTOCOL, "settings": row.get("settings") or {}}


@router.post("/me/guide-seen")
def guide_seen(request: Request,
               authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    row = _storage(lambda: accounts_v4.mark_guide_seen(user["id"]))
    return {"protocol": PROTOCOL, "guide_seen": bool(row.get("guide_seen_at"))}


@router.delete("/me")
def delete_me(request: Request, req: DeleteReq,
              authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    deleted = _storage(lambda: accounts_v4.delete_user(user["id"]))
    if not deleted:
        raise security.not_found()
    return {"protocol": PROTOCOL, "deleted": True}


# --- resume drafts ---------------------------------------------------------------

@router.get("/drafts")
def list_drafts(request: Request,
                authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    return {"protocol": PROTOCOL,
            "drafts": _storage(lambda: accounts_v4.list_drafts(user["id"]))}


@router.put("/drafts")
def save_draft(request: Request, req: DraftReq,
               authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    import json as _json_mod
    if len(_json_mod.dumps(req.fields, ensure_ascii=False)) > 200_000:
        raise _err(422, "DRAFT_TOO_LARGE", "草稿内容过大")
    row = _storage(lambda: accounts_v4.upsert_draft(
        user["id"], req.model_dump(), req.draft_id))
    return {"protocol": PROTOCOL, "draft": row}


@router.get("/drafts/{draft_id}")
def get_draft(request: Request, draft_id: str,
              authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    row = _storage(lambda: accounts_v4.get_draft(user["id"], draft_id))
    if row is None:
        raise security.not_found()
    return {"protocol": PROTOCOL, "draft": row}


@router.delete("/drafts/{draft_id}")
def delete_draft(request: Request, draft_id: str,
                 authorization: str | None = Header(default=None)):
    user = _me(request, authorization)
    if not _storage(lambda: accounts_v4.delete_draft(user["id"], draft_id)):
        raise security.not_found()
    return {"protocol": PROTOCOL, "deleted": draft_id}

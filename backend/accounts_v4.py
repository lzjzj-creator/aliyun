"""v4 single-root account storage (Supabase service-role REST).

Trusted storage primitives, NOT an authorization boundary. Routes must resolve
the Bearer token via session_security_v4 before calling here. app_users rows
never expose password_hash to callers; auth tokens are stored only as sha256.

Guest rows (kind='guest') are created silently on first visit; registration
upgrades the same row in place, so all data keyed by user_id follows the
account with zero migration or claiming.
"""
from datetime import datetime, timezone
import hashlib
from uuid import UUID

try:
    from . import memory
except ImportError:  # FC runs with backend as root
    import memory

_request = memory._request
_json = memory._json
_one = memory._one
MemoryStoreError = memory.MemoryStoreError

USER_FIELDS = ("id,kind,username,init_profile,settings,guide_seen_at,"
               "created_at,last_login_at")
DRAFT_FIELDS = ("id,user_id,template_version,title,fields,evidence_map,gaps,"
                "photo,slug,updated_at")
DRAFT_LIST_FIELDS = "id,slug,template_version,title,updated_at"
TOKEN_MAX_AGE_DAYS = 90


def _uuid(value):
    return str(UUID(str(value)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- app_users ----------------------------------------------------------------

def create_guest() -> dict:
    """Insert a silent guest row. Public fields only."""
    rows = _json(_request("POST", "/app_users", params={"select": USER_FIELDS},
                          headers={"Prefer": "return=representation"},
                          json={"kind": "guest"}))
    if not rows:
        raise MemoryStoreError("Guest insert returned no row")
    return rows[0]


def get_user(user_id: str) -> dict | None:
    return _one(_json(_request("GET", "/app_users", params={
        "id": f"eq.{_uuid(user_id)}", "deleted_at": "is.null",
        "select": USER_FIELDS, "limit": 1,
    })))


def get_user_by_username_lc(username_lc: str) -> dict | None:
    """Includes password_hash for the login path only; callers must strip it."""
    return _one(_json(_request("GET", "/app_users", params={
        "username_lc": f"eq.{username_lc}", "deleted_at": "is.null",
        "kind": "eq.user", "select": USER_FIELDS + ",password_hash", "limit": 1,
    })))


def username_taken(username_lc: str) -> bool:
    rows = _json(_request("GET", "/app_users", params={
        "username_lc": f"eq.{username_lc}", "deleted_at": "is.null",
        "select": "id", "limit": 1,
    }))
    return bool(rows)


def upgrade_to_user(user_id: str, username: str, username_lc: str,
                    password_hash: str) -> dict:
    """Register = upgrade the guest row in place. Data stays keyed by user_id."""
    rows = _json(_request("PATCH", "/app_users", params={
        "id": f"eq.{_uuid(user_id)}", "kind": "eq.guest",
        "deleted_at": "is.null", "select": USER_FIELDS,
    }, headers={"Prefer": "return=representation"}, json={
        "kind": "user", "username": username, "username_lc": username_lc,
        "password_hash": password_hash, "last_login_at": _now(),
    }))
    if not rows:
        raise MemoryStoreError("User upgrade matched no guest row")
    return rows[0]


def touch_login(user_id: str) -> None:
    """Best-effort last_login bump; never raises."""
    try:
        _request("PATCH", "/app_users", params={"id": f"eq.{_uuid(user_id)}"},
                 json={"last_login_at": _now()})
    except MemoryStoreError:
        pass


def save_init_profile(user_id: str, init_profile: dict) -> dict:
    rows = _json(_request("PATCH", "/app_users", params={
        "id": f"eq.{_uuid(user_id)}", "deleted_at": "is.null",
        "select": USER_FIELDS,
    }, headers={"Prefer": "return=representation"},
        json={"init_profile": init_profile}))
    if not rows:
        raise MemoryStoreError("Init profile update matched no user")
    return rows[0]


def save_settings(user_id: str, settings: dict) -> dict:
    rows = _json(_request("PATCH", "/app_users", params={
        "id": f"eq.{_uuid(user_id)}", "deleted_at": "is.null",
        "select": USER_FIELDS,
    }, headers={"Prefer": "return=representation"}, json={"settings": settings}))
    if not rows:
        raise MemoryStoreError("Settings update matched no user")
    return rows[0]


def mark_guide_seen(user_id: str) -> dict:
    rows = _json(_request("PATCH", "/app_users", params={
        "id": f"eq.{_uuid(user_id)}", "deleted_at": "is.null",
        "select": USER_FIELDS,
    }, headers={"Prefer": "return=representation"},
        json={"guide_seen_at": _now()}))
    if not rows:
        raise MemoryStoreError("Guide-seen update matched no user")
    return rows[0]


def delete_user(user_id: str) -> bool:
    """Hard delete; cascades remove tokens/sessions/owner/facts/docs/drafts."""
    rows = _json(_request("DELETE", "/app_users", params={
        "id": f"eq.{_uuid(user_id)}", "select": "id",
    }, headers={"Prefer": "return=representation"}))
    return bool(rows)


def count_assets(user_id: str) -> dict:
    """Approximate asset counts for /me; failures degrade to zeros."""
    counts = {"sessions": 0, "facts": 0, "resumes": 0, "drafts": 0}
    try:
        sessions = _json(_request("GET", "/chat_sessions", params={
            "user_id": f"eq.{_uuid(user_id)}", "select": "id", "limit": 500}))
        counts["sessions"] = len(sessions)
        drafts = _json(_request("GET", "/resume_drafts", params={
            "user_id": f"eq.{_uuid(user_id)}", "select": "id", "limit": 500}))
        counts["drafts"] = len(drafts)
        owner = _one(_json(_request("GET", "/v3_owner", params={
            "user_id": f"eq.{_uuid(user_id)}", "deleted_at": "is.null",
            "select": "id", "limit": 1})))
        if owner:
            facts = _json(_request("GET", "/v3_facts", params={
                "owner_id": f"eq.{owner['id']}", "deleted_at": "is.null",
                "select": "id", "limit": 500}))
            docs = _json(_request("GET", "/v3_resume_documents", params={
                "owner_id": f"eq.{owner['id']}", "deleted_at": "is.null",
                "select": "id", "limit": 500}))
            counts["facts"] = len(facts)
            counts["resumes"] = len(docs)
    except MemoryStoreError:
        pass
    return counts


# --- app_user_tokens ------------------------------------------------------------

def issue_token(user_id: str, token_hash: str) -> dict:
    rows = _json(_request("POST", "/app_user_tokens",
                          params={"select": "token_hash"},
                          headers={"Prefer": "return=representation"},
                          json={"token_hash": token_hash,
                                "user_id": _uuid(user_id)}))
    if not rows:
        raise MemoryStoreError("Token insert returned no row")
    return rows[0]


def resolve_token(token_hash: str) -> dict | None:
    """token_hash -> live app_user row (public fields), or None.

    Lookup goes through the token table then the user row; both misses and
    revoked/expired/deleted rows collapse to None so callers stay uniform.
    """
    if not isinstance(token_hash, str) or len(token_hash) != 64:
        return None
    tok = _one(_json(_request("GET", "/app_user_tokens", params={
        "token_hash": f"eq.{token_hash}", "revoked_at": "is.null",
        "expires_at": f"gt.{_now()}", "select": "user_id", "limit": 1,
    })))
    if tok is None:
        return None
    user = get_user(tok["user_id"])
    if user is None:
        return None
    # Best-effort last_seen bump on the token; failure never blocks a request.
    try:
        _request("PATCH", "/app_user_tokens",
                 params={"token_hash": f"eq.{token_hash}"},
                 json={"last_seen_at": _now()})
    except MemoryStoreError:
        pass
    return user


def revoke_token(token_hash: str) -> None:
    try:
        _request("PATCH", "/app_user_tokens",
                 params={"token_hash": f"eq.{token_hash}"},
                 json={"revoked_at": _now()})
    except MemoryStoreError:
        pass


def revoke_all_tokens(user_id: str) -> None:
    try:
        _request("PATCH", "/app_user_tokens",
                 params={"user_id": f"eq.{_uuid(user_id)}",
                         "revoked_at": "is.null"},
                 json={"revoked_at": _now()})
    except MemoryStoreError:
        pass


# --- resume_drafts --------------------------------------------------------------

def _public_draft(row):
    return {key: row[key] for key in DRAFT_FIELDS.split(",") if key in row}


def list_drafts(user_id: str, limit: int = 50) -> list:
    rows = _json(_request("GET", "/resume_drafts", params={
        "user_id": f"eq.{_uuid(user_id)}", "select": DRAFT_LIST_FIELDS,
        "order": "updated_at.desc,id.desc", "limit": limit,
    }))
    return rows


def _draft_key(user_id: str, draft_id: str) -> dict:
    """draft_id 为 uuid 走主键，否则走 slug（客户端自定义键）。"""
    params = {"user_id": f"eq.{_uuid(user_id)}"}
    try:
        params["id"] = f"eq.{_uuid(draft_id)}"
    except ValueError:
        params["slug"] = f"eq.{draft_id}"
    return params


def get_draft(user_id: str, draft_id: str) -> dict | None:
    row = _one(_json(_request("GET", "/resume_drafts", params={
        **_draft_key(user_id, draft_id),
        "select": DRAFT_FIELDS, "limit": 1,
    })))
    return _public_draft(row) if row else None


def upsert_draft(user_id: str, payload: dict, draft_id: str | None = None) -> dict:
    is_slug = bool(draft_id)
    try:
        if draft_id:
            _uuid(draft_id)
            is_slug = False
    except ValueError:
        pass
    body = {"user_id": _uuid(user_id),
            "template_version": payload.get("template_version") or "",
            "title": (payload.get("title") or "未命名草稿")[:80],
            "fields": payload.get("fields") or {},
            "evidence_map": payload.get("evidence_map") or {},
            "gaps": payload.get("gaps") or [],
            "photo": payload.get("photo"),
            "updated_at": _now()}
    if is_slug:
        body["slug"] = draft_id[:64]
    elif draft_id:
        body["id"] = draft_id    # uuid 键的插入要落到指定 id，而不是另起随机 uuid
    rows = None
    if draft_id:
        rows = _json(_request("PATCH", "/resume_drafts", params={
            **_draft_key(user_id, draft_id), "select": DRAFT_FIELDS,
        }, headers={"Prefer": "return=representation"}, json=body)) or None
    if rows is None:
        rows = _json(_request("POST", "/resume_drafts",
                              params={"select": DRAFT_FIELDS},
                              headers={"Prefer": "return=representation"},
                              json=body))
    if not rows:
        raise MemoryStoreError("Draft upsert returned no row")
    return _public_draft(rows[0])


def delete_draft(user_id: str, draft_id: str) -> bool:
    rows = _json(_request("DELETE", "/resume_drafts", params={
        **_draft_key(user_id, draft_id), "select": "id",
    }, headers={"Prefer": "return=representation"}))
    return bool(rows)

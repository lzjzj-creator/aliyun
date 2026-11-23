"""v2 server-side memory (Supabase service-role REST).

These are trusted storage primitives, NOT an authorization boundary. Routes must
verify_session before bundle/profile/message/delete access. Never return the
service key or session_token_hash. Only create_session returns the raw token.
Profile updates deep-merge objects, replace arrays/scalars, and use CAS versioning;
quote/evidence and confirmed-fact validation belongs to the caller/extractor.
"""
import copy
import hashlib
import hmac
import math
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import load_dotenv

try:
    from .kb_source import to_mirror_url
except ImportError:  # FC runs with backend as root
    from kb_source import to_mirror_url

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
KB_SOURCE_BASE = os.getenv("KB_SOURCE_BASE", "").rstrip("/")
SESSION_FIELDS = "id,anon_id,title,stage,created_at,updated_at,last_message_at"
ROLES = {"user", "assistant", "system"}
STATUSES = {"streaming", "done", "error"}
STAGES = {"greeting", "probing", "wrapping", "done"}
_client = httpx.Client(
    base_url=f"{SUPABASE_URL}/rest/v1",
    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    timeout=30,
)


class MemoryStoreError(RuntimeError):
    """Sanitized storage failure; never includes response/request bodies."""


class ProfileConflict(MemoryStoreError):
    """Profile expected_version is stale. Reload and revalidate before retrying."""


class SessionNotFound(MemoryStoreError):
    """No matching session/profile/message exists."""


def _uuid(value):
    return str(UUID(str(value)))


MESSAGE_TTL_HOURS = 48


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _live_cutoff_iso():
    """48h retention cutoff as a PostgREST literal. Filtering on created_at
    (not the expires_at column) keeps reads correct before the optional
    retention migration lands; expires_at is for the sweeper's index."""
    return (datetime.now(timezone.utc)
            - timedelta(hours=MESSAGE_TTL_HOURS)).isoformat()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _request(method, path, **kwargs):
    try:
        response = _client.request(method, path, **kwargs)
    except httpx.HTTPError:
        raise MemoryStoreError("Supabase transport failure") from None
    return response


def _json(response):
    if not response.is_success:
        raise MemoryStoreError(f"Supabase request failed (HTTP {response.status_code})")
    try:
        return response.json()
    except ValueError:
        raise MemoryStoreError("Supabase returned invalid JSON") from None


def _public_session(row):
    return {key: row[key] for key in SESSION_FIELDS.split(",") if key in row}


def _one(rows):
    return rows[0] if rows else None


def _all(table, params):
    """Page explicitly to avoid Supabase's default 1000-row truncation."""
    rows = []
    offset = 0
    while True:
        page = _json(_request("GET", f"/{table}", params={
            **params, "limit": 500, "offset": offset,
        }))
        rows.extend(page)
        if not page:
            return rows
        offset += len(page)


def create_session(anon_id: str, user_id: str | None = None,
                   seed_profile: dict | None = None) -> dict:
    """Return {session, token}; DB trigger creates empty profile atomically.
    v5: user_id links the session to an app_users row (Bearer path). When
    seed_profile is given, the new session's profile row is pre-filled right
    after insert so initialized users skip basic identity questions."""
    _text(anon_id, "anon_id")
    token = secrets.token_urlsafe(32)
    payload = {
        "anon_id": anon_id,
        "session_token_hash": hashlib.sha256(token.encode()).hexdigest(),
    }
    if user_id:
        payload["user_id"] = str(user_id)
    rows = _json(_request("POST", "/chat_sessions", params={"select": SESSION_FIELDS},
                         headers={"Prefer": "return=representation"}, json=payload))
    if not rows:
        raise MemoryStoreError("Session insert returned no row")
    if seed_profile:
        sid = rows[0]["id"]
        try:
            # Seeding writes profile values only — deliberately NOT the
            # coverage_detail.filled markers, which would lock the slots and
            # block interview corrections of initialized data.
            _json(_request("PATCH", "/user_profiles", params={
                "session_id": f"eq.{sid}",
            }, json={"profile": seed_profile}))
        except MemoryStoreError:
            pass  # Seeding is best-effort; interview still works without it.
    return {"session": _public_session(rows[0]), "token": token}


def verify_session(session_id: str, anon_id: str, token: str) -> bool:
    try:
        session_id = _uuid(session_id)
    except (ValueError, TypeError, AttributeError):
        return False
    if not isinstance(anon_id, str) or not anon_id or not isinstance(token, str) or not token:
        return False
    rows = _json(_request("GET", "/chat_sessions", params={
        "id": f"eq.{session_id}", "anon_id": f"eq.{anon_id}",
        "select": "session_token_hash", "limit": 1,
    }))
    expected = rows[0].get("session_token_hash", "") if rows else "0" * 64
    supplied = hashlib.sha256(token.encode()).hexdigest()
    matched = hmac.compare_digest(supplied, expected)
    return bool(rows) and matched


def list_sessions(anon_id: str, limit: int = 50, user_id: str | None = None) -> list:
    if user_id:
        rows = _json(_request("GET", "/chat_sessions", params={
            "user_id": f"eq.{_uuid(user_id)}", "select": SESSION_FIELDS,
            "order": "updated_at.desc,id.desc", "limit": limit,
        }))
        return [_public_session(row) for row in rows]
    _text(anon_id, "anon_id")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    rows = _json(_request("GET", "/chat_sessions", params={
        "anon_id": f"eq.{anon_id}", "select": SESSION_FIELDS,
        "order": "updated_at.desc,id.desc", "limit": limit,
    }))
    return [_public_session(row) for row in rows]


def session_user_id(session_id: str) -> str | None:
    """v5 ownership check: Bearer user may access any session whose user_id
    column matches, without a session token."""
    row = _one(_json(_request("GET", "/chat_sessions", params={
        "id": f"eq.{_uuid(session_id)}", "select": "user_id", "limit": 1,
    })))
    return row.get("user_id") if row else None


def get_session_bundle(session_id: str) -> dict | None:
    session_id = _uuid(session_id)
    session = _one(_json(_request("GET", "/chat_sessions", params={
        "id": f"eq.{session_id}", "select": SESSION_FIELDS, "limit": 1,
    })))
    if session is None:
        return None
    # v5: 自动 48h 保留已废止——读取不再过滤；history_expired 恒 false（契约位保留）。
    return {"session": _public_session(session), "messages": _all("chat_messages", {
        "session_id": f"eq.{session_id}", "select": "*", "order": "id.asc",
    }), "history_expired": False, "profile": get_profile(session_id)}


def _sources(sources):
    if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
        raise ValueError("sources must be a list of objects")
    result = copy.deepcopy(sources)
    for source in result:
        for field in ("url", "source_url"):
            if source.get(field):
                source[field] = to_mirror_url(source[field], KB_SOURCE_BASE)
    return result


def _message_fields(content=None, steps=None, sources=None):
    result = {}
    if content is not None:
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        result["content"] = content
    if steps is not None:
        if not isinstance(steps, list):
            raise ValueError("steps must be a list")
        result["steps"] = copy.deepcopy(steps)
    if sources is not None:
        result["sources"] = _sources(sources)
    return result


def append_message(session_id: str, role: str, content: str, *,
                   client_msg_id: str | None = None, status: str = "done",
                   steps: list | None = None, sources: list | None = None,
                   meta: dict | None = None) -> dict:
    session_id = _uuid(session_id)
    if role not in ROLES or status not in STATUSES:
        raise ValueError("invalid role or status")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if client_msg_id is not None:
        _text(client_msg_id, "client_msg_id")
    if meta is not None and not isinstance(meta, dict):
        raise ValueError("meta must be an object")
    payload = {"session_id": session_id, "role": role, "status": status,
               "client_msg_id": client_msg_id, "meta": copy.deepcopy(meta or {}),
               **_message_fields(content, steps if steps is not None else [],
                                 sources if sources is not None else [])}
    response = _request("POST", "/chat_messages", json=payload,
                        headers={"Prefer": "return=representation"})
    # The partial unique index covers user messages only. Do not upsert/overwrite
    # previously accepted content. A racing retry returns the committed original.
    if response.status_code == 409 and role == "user" and client_msg_id is not None:
        try:
            duplicate = response.json().get("code") == "23505"
        except (ValueError, AttributeError):
            duplicate = False
        if duplicate:
            old = _one(_json(_request("GET", "/chat_messages", params={
                "session_id": f"eq.{session_id}", "role": "eq.user",
                "client_msg_id": f"eq.{client_msg_id}", "select": "*",
                "limit": 1,
            })))
            if old:
                return old
    rows = _json(response)
    if not rows:
        raise MemoryStoreError("Message insert returned no row")
    return rows[0]


def update_message_status(id: int, status: str, *, content: str | None = None,
                          steps: list | None = None, sources: list | None = None,
                          meta: dict | None = None) -> dict:
    if type(id) is not int or id <= 0 or status not in STATUSES:
        raise ValueError("invalid message id or status")
    if meta is not None and not isinstance(meta, dict):
        raise ValueError("meta must be an object")
    payload = {"status": status, **_message_fields(content, steps, sources)}
    if meta is not None:
        payload["meta"] = copy.deepcopy(meta)
    rows = _json(_request("PATCH", "/chat_messages", params={"id": f"eq.{id}"},
                         headers={"Prefer": "return=representation"}, json=payload))
    if not rows:
        raise SessionNotFound("Message not found")
    return rows[0]


def get_profile(session_id: str) -> dict | None:
    return _one(_json(_request("GET", "/user_profiles", params={
        "session_id": f"eq.{_uuid(session_id)}", "select": "*", "limit": 1,
    })))


def _merge(original, updates):
    result = copy.deepcopy(original)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_profile(session_id: str, updates: dict, expected_version: int, *,
                  coverage: float | None = None, coverage_detail: dict | None = None) -> dict:
    session_id = _uuid(session_id)
    if not isinstance(updates, dict) or type(expected_version) is not int or expected_version < 0:
        raise ValueError("updates must be an object and expected_version a nonnegative integer")
    if coverage is not None and (isinstance(coverage, bool) or not isinstance(coverage, (int, float))
                                 or not math.isfinite(coverage) or not 0 <= coverage <= 1):
        raise ValueError("coverage must be between zero and one")
    if coverage_detail is not None and not isinstance(coverage_detail, dict):
        raise ValueError("coverage_detail must be an object")
    current = get_profile(session_id)
    if current is None:
        raise SessionNotFound("Profile not found")
    if current["version"] != expected_version:
        raise ProfileConflict("Profile version conflict")
    payload = {"profile": _merge(current["profile"], updates), "version": expected_version + 1}
    if coverage is not None:
        payload["coverage"] = coverage
    if coverage_detail is not None:
        payload["coverage_detail"] = copy.deepcopy(coverage_detail)
    rows = _json(_request("PATCH", "/user_profiles", params={
        "session_id": f"eq.{session_id}", "version": f"eq.{expected_version}",
    }, headers={"Prefer": "return=representation"}, json=payload))
    if not rows:
        raise ProfileConflict("Profile version conflict")
    return rows[0]


def delete_session(session_id: str) -> bool:
    rows = _json(_request("DELETE", "/chat_sessions", params={
        "id": f"eq.{_uuid(session_id)}", "select": "id",
    }, headers={"Prefer": "return=representation"}))
    return bool(rows)


def cleanup_expired(days: int = 90, dry_run: bool = True) -> int:
    """Explicit dry_run=False required for physical cascade deletion (FC scheduler)."""
    if type(days) is not int or days < 90 or type(dry_run) is not bool:
        raise ValueError("days must be at least 90 and dry_run must be boolean")
    return int(_json(_request("POST", "/rpc/cleanup_expired_sessions", json={
        "days": days, "dry_run": dry_run,
    })))


def prune_expired_messages(batch_size: int = 500, dry_run: bool = True) -> dict:
    """48h per-message retention sweep. dry_run=False performs physical deletes
    plus title/profile reset and v3 evidence quote stripping (one transaction)."""
    if type(batch_size) is not int or not 1 <= batch_size <= 10000 \
            or type(dry_run) is not bool:
        raise ValueError("batch_size must be 1..10000 and dry_run must be boolean")
    return _json(_request("POST", "/rpc/v2_prune_expired_messages", json={
        "batch_size": batch_size, "dry_run": dry_run,
    }))


def touch_title(session_id: str, first_msg: str) -> dict | None:
    """Only initial user content can set an untouched default title; no overwrite."""
    session_id = _uuid(session_id)
    _text(first_msg, "first_msg")
    first = _one(_json(_request("GET", "/chat_messages", params={
        "session_id": f"eq.{session_id}", "role": "eq.user", "order": "id.asc",
        "select": "content", "limit": 1,
    })))
    if first is None or first["content"] != first_msg:
        return None
    row = _one(_json(_request("PATCH", "/chat_sessions", params={
        "id": f"eq.{session_id}", "title": "eq.新会话", "select": SESSION_FIELDS,
    }, headers={"Prefer": "return=representation"}, json={"title": first_msg[:20]})))
    return _public_session(row) if row is not None else None


def set_session_stage(session_id: str, stage: str) -> dict:
    if stage not in STAGES:
        raise ValueError("invalid stage")
    rows = _json(_request("PATCH", "/chat_sessions", params={
        "id": f"eq.{_uuid(session_id)}", "select": SESSION_FIELDS,
    }, headers={"Prefer": "return=representation"}, json={"stage": stage}))
    if not rows:
        raise SessionNotFound("Session not found")
    return _public_session(rows[0])


# --- Shared quota / stream lease (multi-instance; 20261118_v2_quota.sql) ---------
# In-process buckets cannot bound a fleet, so the economically meaningful caps
# (turns per identity, one stream per session, daily token budget) live here.
QUOTA_WINDOWS = {"ip": 60, "anon": 86400}   # tumbling windows, seconds
LEASE_TTL_SECONDS = 300                      # one turn must not outlive its lease


def new_lease_token() -> str:
    return secrets.token_hex(16)


def spend_quota(scope: str, key: str, limit: int) -> tuple[bool, int]:
    """Shared admission control. Returns (allowed, retry_after_seconds)."""
    if scope not in QUOTA_WINDOWS:
        raise ValueError("unknown quota scope")
    if type(limit) is not int or limit < 1:
        raise ValueError("quota limit must be a positive integer")
    out = _json(_request("POST", "/rpc/v2_spend_quota", json={
        "p_scope": scope, "p_key": _text(key, "quota key")[:200],
        "p_limit": limit, "p_window": QUOTA_WINDOWS[scope],
    }))
    if not isinstance(out, dict) or not isinstance(out.get("allowed"), bool):
        raise MemoryStoreError("Unexpected quota response shape")
    if out["allowed"]:
        return True, 0
    return False, max(1, int(out.get("retry_after") or 1))


def acquire_stream_lease(session_id: str, lease_token: str,
                         ttl: int = LEASE_TTL_SECONDS) -> bool:
    """True when this caller owns the session's single live stream."""
    if type(ttl) is not int or ttl < 1 or ttl > 900:
        raise ValueError("lease ttl must be between 1 and 900 seconds")
    out = _json(_request("POST", "/rpc/v2_acquire_stream_lease", json={
        "p_session_id": _uuid(session_id), "p_lease_token": lease_token, "p_ttl": ttl,
    }))
    return out is True


def release_stream_lease(session_id: str, lease_token: str) -> bool:
    out = _json(_request("POST", "/rpc/v2_release_stream_lease", json={
        "p_session_id": _uuid(session_id), "p_lease_token": lease_token,
    }))
    return bool(out)


def charge_tokens(approx_tokens: int, budget: int) -> tuple[bool, int]:
    """Add daily spend; refused (allowed=False) when it would overshoot the budget."""
    if type(approx_tokens) is not int or approx_tokens < 0:
        raise ValueError("token charge must not be negative")
    if type(budget) is not int or budget < 1:
        raise ValueError("token budget must be positive")
    out = _json(_request("POST", "/rpc/v2_charge_tokens", json={
        "p_tokens": approx_tokens, "p_budget": budget,
    }))
    if not isinstance(out, dict) or not isinstance(out.get("allowed"), bool):
        raise MemoryStoreError("Unexpected token usage response shape")
    return out["allowed"], int(out.get("used") or 0)


def token_usage_today() -> int:
    return int(_json(_request("POST", "/rpc/v2_token_usage_today", json={}) or 0))


def prune_quota(dry_run: bool = True) -> dict:
    """Housekeeping for the daily scheduler: old windows and dead leases."""
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be boolean")
    out = _json(_request("POST", "/rpc/v2_prune_quota", json={"dry_run": dry_run}))
    if not isinstance(out, dict):
        raise MemoryStoreError("Unexpected prune response shape")
    return out

"""v3 owner-level auth: owner_token issuance/verification + recovery codes.

X-Owner-Token is the ONLY credential for /api/v3/*. anon_id is forgeable
plaintext and is never consulted for authorization. Every lookup failure
collapses to 404 per the v2 IDOR convention—"does not exist" and "not yours"
are indistinguishable. No tokens, recovery codes or request bodies are logged.
"""
import hashlib
import hmac
import secrets

from fastapi import HTTPException

try:
    from . import memory_v3, session_security as security
except ImportError:  # FC starts with backend on sys.path
    import memory_v3
    import session_security as security

TOKEN_BYTES = 32
TOKEN_MAX_LEN = 256
RECOVERY_LEN = 20
# RFC 4648 base32 alphabet: no 0/O/1/I, so handwritten codes cannot confuse.
RECOVERY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def new_owner_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_recovery_code() -> str:
    """20-char base32, human-transcribable; stored only as a hash."""
    return "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_LEN))


def normalize_recovery(code) -> str:
    """Uppercase and strip separators so dashed/spaced transcription still works."""
    if not isinstance(code, str):
        return ""
    return "".join(c for c in code.upper() if c in RECOVERY_ALPHABET)


def hash_recovery(code: str) -> str:
    return hashlib.sha256(normalize_recovery(code).encode()).hexdigest()


def verify_recovery(stored_hash, supplied_code) -> bool:
    """Constant-time compare of the stored hash against the supplied code's hash."""
    expected = stored_hash if isinstance(stored_hash, str) and len(stored_hash) == 64 else "0" * 64
    supplied = hash_recovery(supplied_code)
    return hmac.compare_digest(supplied, expected)


def issue_owner(anon_id: str | None) -> dict:
    """Create the owner row and return the one-time plaintext bundle.

    {owner, owner_token, recovery_code} — the token and code appear exactly
    once; afterwards only their sha256 hashes exist server-side.
    """
    owner_token = new_owner_token()
    recovery_code = new_recovery_code()
    owner = memory_v3.enable_owner(anon_id, hash_token(owner_token),
                                   hash_recovery(recovery_code))
    return {"owner": owner, "owner_token": owner_token,
            "recovery_code": recovery_code}


def require_owner(x_owner_token) -> dict:
    """X-Owner-Token -> live enabled owner row, or 404/503. Best-effort bumps
    last_seen_at (the 365-day retention clock) without failing the request."""
    if not isinstance(x_owner_token, str) or not 1 <= len(x_owner_token) <= TOKEN_MAX_LEN:
        raise security.not_found()
    try:
        owner = memory_v3.get_owner_by_key_hash(hash_token(x_owner_token))
    except memory_v3.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None
    if owner is None or not owner.get("memory_enabled"):
        raise security.not_found()
    memory_v3.touch_owner(owner["id"])
    return owner


def redeem_recovery(recovery_code) -> dict:
    """Recovery-code redemption: a fresh owner_token plus a rotated code.

    The schema stores a single owner_key_hash, so issuing a new token always
    replaces the hash—any previously issued token stops working immediately
    (this is the §4 "revoke old" semantic; there is no parallel-key mode).
    A wrong/expended code returns the same 404 as an unknown one—no oracle.
    """
    owner = memory_v3.get_owner_by_recovery_hash(hash_recovery(recovery_code))
    if owner is None:
        raise security.not_found()
    new_token = new_owner_token()
    new_code = new_recovery_code()
    updated = memory_v3.rotate_owner_key(
        owner["id"], hash_token(new_token), hash_recovery(new_code))
    return {"owner": updated, "owner_token": new_token, "recovery_code": new_code}

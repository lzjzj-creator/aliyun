"""v4 account auth: Bearer token resolution + scrypt password hashing.

Authorization: Bearer <auth_token> identifies an app_users row (guest or user).
No tokens, passwords or request bodies are logged. Every credential failure
collapses to the v2/v3 IDOR convention: a uniform 404 (and 401 only where the
client needs to distinguish "log in first" from "not yours").

Passwords use stdlib hashlib.scrypt (N=2^14, r=8, p=1) with a random salt;
the stored format is scrypt$N$r$p$salt_hex$hash_hex — no new dependencies.
"""
import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException, Request

try:
    from . import accounts_v4, session_security as security
except ImportError:  # FC starts with backend on sys.path
    import accounts_v4
    import session_security as security

TOKEN_BYTES = 32
TOKEN_MAX_LEN = 256
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
DKLEN = 32
USERNAME_MIN, USERNAME_MAX = 2, 24
PASSWORD_MIN, PASSWORD_MAX = 8, 128


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt,
                             n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DKLEN)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time compare; malformed stored hashes fail closed."""
    try:
        scheme, n, r, p, salt_hex, hash_hex = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                 n=int(n), r=int(r), p=int(p), dklen=DKLEN)
        return hmac.compare_digest(derived.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def _bearer_token(authorization: str | None) -> str | None:
    if not isinstance(authorization, str):
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    token = token.strip()
    return token if 1 <= len(token) <= TOKEN_MAX_LEN else None


def optional_user(request: Request,
                  authorization: str | None = Header(default=None)) -> dict | None:
    """Bearer -> app_user row, or None; storage error → 503. Guests count as
    users. `request` is unused (kept for signature symmetry with require_user)."""
    token = _bearer_token(authorization)
    if token is None:
        return None
    try:
        return accounts_v4.resolve_token(hash_token(token))
    except accounts_v4.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None


def require_user(request: Request,
                 authorization: str | None = Header(default=None)) -> dict:
    """Bearer -> app_user row; missing/invalid credential is a uniform 404."""
    security.enforce_ip(request)
    user = optional_user(request, authorization)
    if user is None:
        raise security.not_found()
    return user


def bearer_token_hash(request: Request,
                      authorization: str | None = Header(default=None)) -> str | None:
    token = _bearer_token(authorization)
    return hash_token(token) if token else None


def issue_auth(user_id: str) -> str:
    """Mint a token for the user row and return the plaintext exactly once."""
    token = new_token()
    accounts_v4.issue_token(user_id, hash_token(token))
    return token

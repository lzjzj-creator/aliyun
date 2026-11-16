"""管理员密码与会话令牌原语（不依赖 Web 框架，便于脚本复用）。"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

SESSION_TTL = int(os.getenv("ADMIN_SESSION_TTL", "7200"))


def password_hash() -> str:
    return os.getenv("ADMIN_PASSWORD_HASH", "")


def session_secret() -> str:
    return os.getenv("ADMIN_SESSION_SECRET", "")


def admin_ready() -> bool:
    return password_hash().startswith("pbkdf2_sha256$") and len(session_secret()) >= 32


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, iterations: int = 310_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, encoded: str | None = None) -> bool:
    encoded = encoded or password_hash()
    try:
        algo, iterations, salt, expected = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _b64d(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(_b64e(digest), expected)


def issue_session(secret: str | None = None, ttl: int = SESSION_TTL,
                  now: float | None = None) -> str:
    secret = session_secret() if secret is None else secret
    now = int(time.time() if now is None else now)
    payload = {"sub": "admin", "iat": now, "exp": now + ttl, "nonce": secrets.token_hex(8)}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64e(signature)}"


def verify_session(token: str, secret: str | None = None, now: float | None = None) -> bool:
    secret = session_secret() if secret is None else secret
    if not token or not secret:
        return False
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(signature), expected):
            return False
        payload = json.loads(_b64d(body))
        return (payload.get("sub") == "admin"
                and int(payload.get("exp", 0)) > int(time.time() if now is None else now))
    except (ValueError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return False
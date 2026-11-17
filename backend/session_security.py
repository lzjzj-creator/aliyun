"""Single-instance v2 ownership and rate limits; no request bodies or tokens logged.

XFF's last hop is trusted to match the existing FC gateway contract. Do not use
this configuration behind an untrusted proxy or with multiple FC instances.
"""
import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from uuid import UUID

from fastapi import Header, HTTPException, Request

try:
    from . import memory
except ImportError:  # FC starts with backend on sys.path
    import memory


def not_found():
    return HTTPException(404, "资源不存在")


def throttled(retry=1):
    return HTTPException(429, "请求过于频繁", headers={"Retry-After": str(max(1, math.ceil(retry)))})


class RateLimiter:
    """Rolling buckets, bounded identities and fail-closed admission at capacity."""
    def __init__(self, *, clock=time.monotonic, capacity=20000):
        self._clock = clock
        self._capacity = capacity
        self._lock = threading.Lock()
        self._ip = {}
        self._anon = {}
        self._streams = set()

    def _allow(self, buckets, key, window, limit):
        now = self._clock()
        with self._lock:
            if key not in buckets and len(buckets) >= self._capacity:
                for old_key, old_bucket in list(buckets.items()):
                    while old_bucket and now - old_bucket[0] >= window:
                        old_bucket.popleft()
                    if not old_bucket:
                        del buckets[old_key]
                if len(buckets) >= self._capacity:
                    return False, 1
            bucket = buckets.setdefault(key, deque())
            while bucket and now - bucket[0] >= window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False, max(1, math.ceil(window - (now - bucket[0])))
            bucket.append(now)
            return True, 0

    def check_ip(self, key):
        return self._allow(self._ip, key, 60, 20)

    def check_anon(self, key):
        return self._allow(self._anon, key, 86400, 100)

    @contextmanager
    def stream(self, session_id):
        session_id = str(UUID(str(session_id)))
        with self._lock:
            if session_id in self._streams or len(self._streams) >= self._capacity:
                raise throttled()
            self._streams.add(session_id)
        try:
            yield
        finally:
            with self._lock:
                self._streams.discard(session_id)


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[-1].strip() or "unknown"
    return (request.client.host if request.client else None) or "unknown"


def enforce_ip(request: Request):
    ok, retry = limiter.check_ip(client_ip(request))
    if not ok:
        raise throttled(retry)


def require_anon(x_anon_id: str | None = Header(default=None)) -> str:
    # Frontend generates crypto.randomUUID(); normalize so case/spelling cannot
    # split the same identity across quota buckets.
    if not isinstance(x_anon_id, str) or len(x_anon_id) > 64:
        raise not_found()
    try:
        return str(UUID(x_anon_id))
    except (ValueError, AttributeError):
        raise not_found() from None


def require_owner(session_id: str, anon_id: str, token: str | None):
    if not isinstance(token, str) or not 1 <= len(token) <= 256:
        raise not_found()
    try:
        canonical_id = str(UUID(session_id))
    except (ValueError, AttributeError):
        raise not_found() from None
    try:
        valid = memory.verify_session(canonical_id, anon_id, token)
    except memory.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None
    if not valid:
        raise not_found()
    return canonical_id


def charge_turn(anon_id: str):
    ok, retry = limiter.check_anon(anon_id)
    if not ok:
        raise throttled(retry)

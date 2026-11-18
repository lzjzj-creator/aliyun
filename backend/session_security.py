"""v2 ownership and rate limits; no request bodies or tokens logged.

XFF's last hop is trusted to match the existing FC gateway contract. Do not use
this configuration behind an untrusted proxy.

Two limiter backends:
  * RateLimiter  — in-process buckets. Correct only for a single FC instance.
  * SharedLimiter — Supabase-backed turn quota and per-session stream lease, so the
    economically meaningful caps hold across instances (V2_SHARED_QUOTA=1).
The per-IP minute bucket deliberately stays in-process: it bounds request spam,
while turns/day and the daily token budget (both shared) bound real spend.
"""
import math
import os
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


IP_LIMIT_PER_MIN = int(os.getenv("V2_IP_LIMIT_PER_MIN", "20"))
ANON_TURNS_PER_DAY = int(os.getenv("V2_ANON_TURNS_PER_DAY", "100"))
SHARED_QUOTA_ENABLED = os.getenv("V2_SHARED_QUOTA", "0") == "1"


def not_found():
    return HTTPException(404, "资源不存在")


def throttled(retry=1):
    return HTTPException(429, "请求过于频繁", headers={"Retry-After": str(max(1, math.ceil(retry)))})


def unavailable():
    return HTTPException(503, "配额存储暂时不可用")


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
        return self._allow(self._ip, key, 60, IP_LIMIT_PER_MIN)

    def check_anon(self, key):
        return self._allow(self._anon, key, 86400, ANON_TURNS_PER_DAY)

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


class SharedLimiter:
    """Turn quota and stream lease in Postgres, so a fleet shares one accounting.

    Fail-closed on store errors: an unbounded FC fleet must not silently drop the
    only caps that limit spend. The IP minute bucket stays local (spam shaping, not
    spend), served by the embedded RateLimiter.
    """
    def __init__(self, *, store=memory, local=None, clock=time.monotonic):
        self._store = store
        self._local = local or RateLimiter()
        self._clock = clock

    def check_ip(self, key):
        return self._local.check_ip(key)

    def check_anon(self, key):
        try:
            return self._store.spend_quota("anon", key, ANON_TURNS_PER_DAY)
        except self._store.MemoryStoreError:
            raise unavailable() from None

    @contextmanager
    def stream(self, session_id):
        session_id = str(UUID(str(session_id)))
        lease = self._store.new_lease_token()
        try:
            owned = self._store.acquire_stream_lease(session_id, lease)
        except self._store.MemoryStoreError:
            raise unavailable() from None
        if not owned:
            raise throttled(5)
        try:
            yield
        finally:
            try:  # a lost release only costs the remaining lease TTL
                self._store.release_stream_lease(session_id, lease)
            except self._store.MemoryStoreError:
                pass


def build_limiter():
    return SharedLimiter() if SHARED_QUOTA_ENABLED else RateLimiter()


limiter = build_limiter()


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

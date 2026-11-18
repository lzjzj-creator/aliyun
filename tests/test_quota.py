"""Offline tests for the shared (cross-instance) quota/lease/budget layer.

The Postgres RPCs themselves are exercised against a real PostgreSQL by
tests/v2_db_acceptance.sh; here we only pin the HTTP contract, the limiter
behaviour and the fail-closed choices. No services, no real credentials.
"""
import importlib
import json
import math
import os
import re
import unittest
from datetime import date
from unittest.mock import patch

import httpx

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
}):
    memory = importlib.import_module("backend.memory")

SID = "12345678-1234-4234-8234-123455789abc"


class RpcStore:
    """Tumbling-window / lease semantics close enough to catch contract drift."""
    def __init__(self):
        self.buckets = {}
        self.leases = {}
        self.usage = 0
        self.calls = []
        self.fail = None

    def __call__(self, request):
        if self.fail:
            raise self.fail(request)
        name = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((name, payload))
        if name == "v2_spend_quota":
            scope, key, limit, window = (payload["p_scope"], payload["p_key"],
                                         payload["p_limit"], payload["p_window"])
            window_start = math.floor(1_700_000_000 / window) * window
            row = self.buckets.setdefault((scope, key, window_start), 0)
            if row >= limit:
                return httpx.Response(200, json={"allowed": False, "hits": row, "retry_after": window})
            self.buckets[(scope, key, window_start)] = row + 1
            return httpx.Response(200, json={"allowed": True, "hits": row + 1, "retry_after": 0})
        if name == "v2_acquire_stream_lease":
            sid, token = payload["p_session_id"], payload["p_lease_token"]
            holder = self.leases.get(sid)
            if holder is None or holder == token:
                self.leases[sid] = token
                return httpx.Response(200, json=True)
            return httpx.Response(200, json=False)
        if name == "v2_release_stream_lease":
            sid, token = payload["p_session_id"], payload["p_lease_token"]
            if self.leases.get(sid) == token:
                del self.leases[sid]
                return httpx.Response(200, json=1)
            return httpx.Response(200, json=0)
        if name == "v2_charge_tokens":
            tokens, budget = payload["p_tokens"], payload["p_budget"]
            if self.usage + tokens > budget:
                return httpx.Response(200, json={"allowed": False, "used": self.usage, "budget": budget})
            self.usage += tokens
            return httpx.Response(200, json={"allowed": True, "used": self.usage, "budget": budget})
        if name == "v2_token_usage_today":
            return httpx.Response(200, json=self.usage)
        if name == "v2_prune_quota":
            return httpx.Response(200, json={"quota_buckets": 0, "stream_leases": 0})
        return httpx.Response(404, json={"message": "unexpected rpc"})


class QuotaPrimitivesTest(unittest.TestCase):
    def setUp(self):
        self.store = RpcStore()
        self.client = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            headers={"apikey": "k", "Authorization": "Bearer k"},
            transport=httpx.MockTransport(self.store),
        )
        patcher = patch.object(memory, "_client", self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.client.close)

    def test_spend_quota_uses_named_rpc_args(self):
        allowed, retry = memory.spend_quota("anon", SID, 100)
        self.assertTrue(allowed)
        self.assertEqual(retry, 0)      # admitted → no Retry-After is implied
        name, payload = self.store.calls[-1]
        self.assertEqual(name, "v2_spend_quota")
        self.assertEqual(sorted(payload), ["p_key", "p_limit", "p_scope", "p_window"])
        self.assertEqual(payload["p_window"], memory.QUOTA_WINDOWS["anon"])

    def test_spend_quota_denies_when_window_saturated(self):
        for _ in range(3):
            memory.spend_quota("ip", "1.2.3.4", 3)
        allowed, retry = memory.spend_quota("ip", "1.2.3.4", 3)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)

    def test_unknown_scope_and_bad_limits_are_rejected_locally(self):
        for args in (("session", SID, 5), ("anon", "  ", 5), ("anon", SID, 0)):
            with self.assertRaises(ValueError):
                memory.spend_quota(*args)

    def test_lease_round_trip_and_owner_only_release(self):
        token = memory.new_lease_token()
        self.assertRegex(token, r"^[0-9a-f]{32}$")
        self.assertTrue(memory.acquire_stream_lease(SID, token))
        self.assertFalse(memory.acquire_stream_lease(SID, memory.new_lease_token()))
        self.assertFalse(memory.release_stream_lease(SID, "0" * 32))   # not my lease
        self.assertTrue(memory.release_stream_lease(SID, token))
        self.assertTrue(memory.acquire_stream_lease(SID, token))       # free again
        with self.assertRaises(ValueError):
            memory.acquire_stream_lease(SID, token, ttl=9999)
        with self.assertRaises(ValueError):
            memory.acquire_stream_lease("not-a-uuid", token)

    def test_charge_tokens_and_today_read(self):
        self.assertEqual(memory.charge_tokens(120, 1_000), (True, 120))
        self.assertEqual(memory.token_usage_today(), 120)
        allowed, used = memory.charge_tokens(5_000, 1_000)
        self.assertFalse(allowed)
        self.assertEqual(used, 120)      # overshoot is refused, not partially charged
        with self.assertRaises(ValueError):
            memory.charge_tokens(-1, 1_000)
        with self.assertRaises(ValueError):
            memory.charge_tokens(10, 0)

    def test_prune_quota_requires_explicit_boolean(self):
        self.assertEqual(memory.prune_quota(dry_run=True), {"quota_buckets": 0, "stream_leases": 0})
        with self.assertRaises(ValueError):
            memory.prune_quota(dry_run=None)

    def test_transport_errors_stay_sanitized(self):
        def boom(request):
            raise httpx.ConnectError("secret https://supabase.invalid?servicekey=leak")
        with self.assertRaises(memory.MemoryStoreError) as ctx:
            with patch.object(memory, "_client", httpx.Client(
                    base_url="https://supabase.invalid/rest/v1",
                    transport=httpx.MockTransport(boom))):
                memory.token_usage_today()
        self.assertNotIn("leak", str(ctx.exception))
        self.assertNotIn("supabase.invalid", str(ctx.exception))


class SharedLimiterTest(unittest.TestCase):
    def setUp(self):
        self.store = RpcStore()
        self.client = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            transport=httpx.MockTransport(self.store),
        )
        patcher = patch.object(memory, "_client", self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.client.close)
        self.security = importlib.import_module("backend.session_security")
        self.limiter = self.security.SharedLimiter()

    def test_turn_quota_is_shared_not_per_process(self):
        with patch.object(self.security, "ANON_TURNS_PER_DAY", 2):
            self.assertTrue(self.limiter.check_anon("anon-1")[0])
            self.assertTrue(self.limiter.check_anon("anon-1")[0])
            allowed, retry = self.limiter.check_anon("anon-1")
            self.assertFalse(allowed)
            self.assertGreaterEqual(retry, 1)
            self.assertTrue(self.limiter.check_anon("anon-2")[0])   # other identity unaffected

    def test_stream_lease_denies_second_holder_with_429(self):
        with self.limiter.stream(SID):
            with self.assertRaises(self.security.HTTPException) as ctx:
                with self.limiter.stream(SID):
                    pass
            self.assertEqual(ctx.exception.status_code, 429)
            self.assertEqual(ctx.exception.headers.get("Retry-After"), "5")
        # released → next holder gets in
        with self.limiter.stream(SID):
            pass

    def test_store_outage_fails_closed(self):
        def boom(request):
            raise httpx.ConnectError("down")
        with patch.object(memory, "_client", httpx.Client(
                base_url="https://supabase.invalid/rest/v1", transport=httpx.MockTransport(boom))):
            with self.assertRaises(self.security.HTTPException) as ctx:
                self.limiter.check_anon("anon-1")
            self.assertEqual(ctx.exception.status_code, 503)
            with self.assertRaises(self.security.HTTPException) as ctx:
                with self.limiter.stream(SID):
                    pass
            self.assertEqual(ctx.exception.status_code, 503)

    def test_lost_release_is_swallowed_and_ip_stays_local(self):
        with patch.object(memory, "release_stream_lease",
                          side_effect=memory.MemoryStoreError("nope")):
            with self.limiter.stream(SID):     # must not explode after a good turn
                pass
        local_calls = []
        with patch.object(self.limiter._local, "check_ip",
                          side_effect=lambda k: local_calls.append(k) or (True, 0)):
            self.limiter.check_ip("9.9.9.9")
        self.assertEqual(local_calls, ["9.9.9.9"])

    def test_backend_selection_follows_flag(self):
        with patch.object(self.security, "SHARED_QUOTA_ENABLED", True):
            self.assertIsInstance(self.security.build_limiter(), self.security.SharedLimiter)
        with patch.object(self.security, "SHARED_QUOTA_ENABLED", False):
            self.assertIsInstance(self.security.build_limiter(), self.security.RateLimiter)


class CoachBudgetTest(unittest.TestCase):
    def setUp(self):
        self.coach = importlib.import_module("backend.coach")
        self.store = RpcStore()
        self.client = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            transport=httpx.MockTransport(self.store),
        )
        patcher = patch.object(memory, "_client", self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.client.close)
        self.addCleanup(self.coach._budget_read.update, {"at": 0.0, "used": 0})

    def test_local_counter_when_shared_disabled(self):
        with patch.object(self.coach, "SHARED_BUDGET", False):
            self.coach._day_budget.update(date="", used=0)
            self.assertTrue(self.coach.budget_ok())
            with patch.object(self.coach, "DAILY_TOKEN_BUDGET", 10):
                self.coach.charge_tokens(90)      # 90 chars ≈ 30 tokens > 10
                self.assertFalse(self.coach.budget_ok())
        self.assertEqual(self.store.calls, [])    # no storage round-trip at all

    def test_shared_budget_blocks_at_cap_and_caches_reads(self):
        with patch.object(self.coach, "SHARED_BUDGET", True), \
                patch.object(self.coach, "DAILY_TOKEN_BUDGET", 100):
            self.assertTrue(self.coach.budget_ok())
            reads = len([c for c in self.store.calls if c[0] == "v2_token_usage_today"])
            self.coach.budget_ok()
            self.assertEqual(len([c for c in self.store.calls if c[0] == "v2_token_usage_today"]),
                             reads)                          # served from the 30s cache
            self.coach.charge_tokens(300)                     # ≈100 tokens → at cap
            self.assertFalse(self.coach.budget_ok())

    def test_unknown_spend_fails_closed(self):
        def boom(request):
            raise httpx.ConnectError("down")
        with patch.object(self.coach, "SHARED_BUDGET", True), patch.object(
                self.coach, "_budget_read", {"at": 0.0, "used": 0}), patch.object(
                memory, "_client", httpx.Client(base_url="https://supabase.invalid/rest/v1",
                                                transport=httpx.MockTransport(boom))):
            self.assertFalse(self.coach.budget_ok())

    def test_finished_turn_survives_charge_failure(self):
        with patch.object(self.coach, "SHARED_BUDGET", True), patch.object(
                memory, "charge_tokens", side_effect=memory.MemoryStoreError("down")):
            self.assertIsNone(self.coach.charge_tokens(300))


if __name__ == "__main__":
    unittest.main()

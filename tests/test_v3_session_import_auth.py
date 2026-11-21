"""HTTP authorization regressions: owner tokens never grant source-chat access."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://supabase.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-only")
os.environ.setdefault("DASHSCOPE_API_KEY", "test-only")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.routers import memory_v3 as routes

REAL_VERIFY_SESSION = routes.security.memory.verify_session

OID = "12345678-1234-4234-8234-123456789abc"
ANON = "aaaaaaaa-1234-4234-8234-123456789abc"
SID = "87654321-4321-4321-8321-cba987654321"
OTHER = "87654321-4321-4321-8321-cba987654322"
OWNER = {"id": OID, "memory_enabled": True, "anon_id": ANON}
HEADERS = {"X-Owner-Token": "owner-token", "X-Anon-Id": ANON}


class SessionImportAuthTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        routes._extract_hits.clear()
        routes._backfill_hits.clear()
        for target, name, kwargs in [
            (routes.security, "enforce_ip", {"return_value": None}),
            (routes.security_v3, "require_owner", {"return_value": OWNER}),
            (routes.memory_v3, "spend_daily_quota", {"return_value": 1}),
            (routes.fact_extractor, "extract_incremental", {"return_value": {"added": 1}}),
            (routes.security.memory, "verify_session", {
                "side_effect": lambda sid, anon, token: sid == SID and anon == ANON and token == "right-token"}),
        ]:
            p = patch.object(target, name, **kwargs)
            obj = p.start()
            self.addCleanup(p.stop)
            setattr(self, name, obj)

    def extract(self, headers):
        return self.client.post("/api/v3/facts/extract", headers=headers,
                                json={"session_id": SID})

    def test_owner_only_missing_wrong_and_forged_anon_never_read_chat(self):
        for headers in [
            {"X-Owner-Token": "owner-token"}, HEADERS,
            {**HEADERS, "X-Session-Token": "wrong-token"},
            {**HEADERS, "X-Anon-Id": OTHER, "X-Session-Token": "right-token"},
        ]:
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.extract(headers).status_code, 404)
        self.extract_incremental.assert_not_called()
        self.spend_daily_quota.assert_not_called()

    def test_real_v2_hash_verification_rejects_owner_token_as_session_proof(self):
        import hashlib
        import httpx
        reads = []
        def store(request):
            reads.append(request)
            self.assertTrue(request.url.path.endswith("/chat_sessions"))
            params = request.url.params
            self.assertEqual(params["select"], "session_token_hash")
            rows = [{"session_token_hash": hashlib.sha256(b"right-token").hexdigest()}]
            if params["id"] != "eq." + SID or params["anon_id"] != "eq." + ANON:
                rows = []
            return httpx.Response(200, json=rows)
        with httpx.Client(base_url="https://supabase.invalid/rest/v1", transport=httpx.MockTransport(store)) as client, \
             patch.object(routes.security.memory, "_client", client), \
             patch.object(routes.security.memory, "verify_session", REAL_VERIFY_SESSION):
            self.assertEqual(self.extract({**HEADERS, "X-Session-Token": "owner-token"}).status_code, 404)
            self.extract_incremental.assert_not_called()
            self.assertEqual(self.extract({**HEADERS, "X-Session-Token": "right-token"}).status_code, 200)
        self.assertEqual(len(reads), 2)
        self.extract_incremental.assert_called_once_with(OID, SID)

    def test_valid_session_proof_imports_to_owner_memory(self):
        result = self.extract({**HEADERS, "X-Session-Token": "right-token"})
        self.assertEqual(result.status_code, 200)
        self.verify_session.assert_called_once_with(SID, ANON, "right-token")
        self.extract_incremental.assert_called_once_with(OID, SID)

    def test_backfill_checks_each_session_before_quota_or_extraction(self):
        result = self.client.post("/api/v3/memory/backfill", headers=HEADERS, json={
            "session_ids": [SID, OTHER],
            "session_tokens": {SID: "right-token", OTHER: "wrong-token"},
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["results"], [
            {"session_id": SID, "added": 1}, {"session_id": OTHER, "error": "not_found"}])
        self.extract_incremental.assert_called_once_with(OID, SID)
        self.spend_daily_quota.assert_called_once()

    def test_backfill_never_enumerates_sessions_by_forgeable_anon(self):
        with patch.object(routes.security.memory, "list_sessions",
                          side_effect=AssertionError("must not enumerate")):
            empty = self.client.post("/api/v3/memory/backfill", headers=HEADERS, json={})
            self.assertEqual(empty.status_code, 200)
            self.assertEqual(empty.json()["processed"], 0)
            result = self.client.post("/api/v3/memory/backfill", headers=HEADERS,
                                     json={"session_tokens": {SID: "right-token"}})
            self.assertEqual(result.json()["results"], [{"session_id": SID, "added": 1}])
        self.extract_incremental.assert_called_once_with(OID, SID)

    def test_backfill_no_tokens_is_not_found_and_no_token_is_echoed(self):
        result = self.client.post("/api/v3/memory/backfill", headers=HEADERS,
                                 json={"session_ids": [SID]})
        self.assertEqual(result.json()["results"], [{"session_id": SID, "error": "not_found"}])
        self.extract_incremental.assert_not_called()
        self.spend_daily_quota.assert_not_called()

    def test_verify_store_failure_is_503_and_fails_closed(self):
        self.verify_session.side_effect = routes.security.memory.MemoryStoreError("offline")
        result = self.extract({**HEADERS, "X-Session-Token": "right-token"})
        self.assertEqual(result.status_code, 503)
        result = self.client.post("/api/v3/memory/backfill", headers=HEADERS,
                                 json={"session_tokens": {SID: "right-token"}})
        self.assertEqual(result.status_code, 503)
        self.extract_incremental.assert_not_called()

    def test_bounded_token_map_and_canonical_deduplicated_ids(self):
        for body in [{"session_tokens": {"bogus": "token"}},
                     {"session_tokens": {SID: "x" * 257}}]:
            result = self.client.post("/api/v3/memory/backfill", headers=HEADERS, json=body)
            self.assertEqual(result.status_code, 422)
        result = self.client.post("/api/v3/memory/backfill", headers=HEADERS, json={
            "session_ids": [SID.upper(), SID], "session_tokens": {SID.upper(): "right-token"}})
        self.assertEqual(result.json()["processed"], 1)
        self.extract_incremental.assert_called_once_with(OID, SID)


if __name__ == "__main__":
    unittest.main()

import os
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request

from backend.admin import _client_key
from backend.admin_auth import (admin_ready, hash_password, issue_session,
                                verify_password, verify_session)

SECRET = "test-secret-" + "x" * 32


class PasswordTest(unittest.TestCase):
    def test_roundtrip(self):
        encoded = hash_password("correct horse", iterations=1000)
        self.assertTrue(verify_password("correct horse", encoded))

    def test_wrong_password(self):
        encoded = hash_password("correct horse", iterations=1000)
        self.assertFalse(verify_password("wrong", encoded))

    def test_malformed_hash(self):
        self.assertFalse(verify_password("x", "plain-text"))
        self.assertFalse(verify_password("x", "pbkdf2_sha256$bad"))

    def test_admin_ready_requires_hash_and_secret(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": "", "ADMIN_SESSION_SECRET": ""}):
            self.assertFalse(admin_ready())
        with patch.dict(os.environ, {
            "ADMIN_PASSWORD_HASH": hash_password("x", iterations=1000),
            "ADMIN_SESSION_SECRET": "short",
        }):
            self.assertFalse(admin_ready())
        with patch.dict(os.environ, {
            "ADMIN_PASSWORD_HASH": hash_password("x", iterations=1000),
            "ADMIN_SESSION_SECRET": SECRET,
        }):
            self.assertTrue(admin_ready())


class SessionTest(unittest.TestCase):
    def test_valid_token(self):
        token = issue_session(SECRET, ttl=600, now=1000)
        self.assertTrue(verify_session(token, SECRET, now=1200))

    def test_expired_token(self):
        token = issue_session(SECRET, ttl=60, now=1000)
        self.assertFalse(verify_session(token, SECRET, now=1061))

    def test_tampered_payload(self):
        token = issue_session(SECRET, ttl=600, now=1000)
        body, signature = token.split(".", 1)
        forged = ("A" if body[0] != "A" else "B") + body[1:]
        self.assertFalse(verify_session(f"{forged}.{signature}", SECRET, now=1100))

    def test_tampered_signature(self):
        token = issue_session(SECRET, ttl=600, now=1000)
        body, signature = token.split(".", 1)
        forged = ("A" if signature[0] != "A" else "B") + signature[1:]
        self.assertFalse(verify_session(f"{body}.{forged}", SECRET, now=1100))

    def test_wrong_secret(self):
        token = issue_session(SECRET, ttl=600, now=1000)
        self.assertFalse(verify_session(token, SECRET + "y", now=1100))

    def test_malformed_token(self):
        for token in ("", "abc", "a.b.c", "!!!.???"):
            self.assertFalse(verify_session(token, SECRET, now=1100))


class ClientKeyTest(unittest.TestCase):
    def request(self, xff=None, client=("10.0.0.9", 1234)):
        headers = [] if xff is None else [(b"x-forwarded-for", xff.encode())]
        return Request({"type": "http", "headers": headers, "client": client})

    def test_uses_last_xff_hop(self):
        self.assertEqual(_client_key(self.request("198.51.100.1, 203.0.113.7")), "203.0.113.7")

    def test_strips_last_hop_whitespace(self):
        self.assertEqual(_client_key(self.request("198.51.100.1 , 203.0.113.7 ")), "203.0.113.7")

    def test_single_xff_value(self):
        self.assertEqual(_client_key(self.request("203.0.113.7")), "203.0.113.7")

    def test_no_xff_falls_back_to_client_host(self):
        self.assertEqual(_client_key(self.request(client=("192.0.2.5", 443))), "192.0.2.5")

    def test_blank_xff_falls_back_to_client_host(self):
        self.assertEqual(_client_key(self.request("   ", client=("192.0.2.6", 80))), "192.0.2.6")

    def test_missing_client_uses_unknown(self):
        self.assertEqual(_client_key(self.request(client=None)), "unknown")


class CleanupEndpointTest(unittest.TestCase):
    """POST /api/admin/cleanup: admin-gated retention preview/execute, counts only."""
    def build(self, *, override):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend import admin

        app = FastAPI()
        app.include_router(admin.router)
        if override:
            app.dependency_overrides[admin.require_admin] = lambda: None
        return TestClient(app)

    def setUp(self):
        from backend import admin

        self.admin = admin
        self.calls = []
        self.client = self.build(override=True)

        class FakeMemory:
            MemoryStoreError = RuntimeError

            def __init__(self, outer):
                self.outer = outer

            def cleanup_expired(self, days, dry_run):
                self.outer.calls.append(("cleanup", days, dry_run))
                return 3

            def prune_quota(self, dry_run):
                self.outer.calls.append(("prune", dry_run))
                return {"quota_buckets": 1, "stream_leases": 0}

        self.patch = patch.object(admin, "_memory", FakeMemory(self))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def post(self, body, token=None, client=None):
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        return (client or self.client).post("/api/admin/cleanup", json=body, headers=headers)

    def test_defaults_to_dry_run_and_returns_only_counts(self):
        response = self.post({})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, [("cleanup", 90, True), ("prune", True)])
        self.assertEqual(response.json()["data"], {
            "days": 90, "dry_run": True, "expired_sessions": 3,
            "quota": {"quota_buckets": 1, "stream_leases": 0}})

    def test_explicit_delete_passes_through(self):
        response = self.post({"days": 120, "dry_run": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[0], ("cleanup", 120, False))
        self.assertEqual(self.calls[1], ("prune", False))

    def test_retention_shorter_than_90_is_rejected_before_storage(self):
        for body in ({"days": 30}, {"days": 90, "dry_run": "yes"}, {"days": "90"}, {"dry_run": None}):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 422)
        self.assertEqual(self.calls, [])

    def test_store_outage_is_sanitized_503(self):
        class Outage:
            MemoryStoreError = RuntimeError

            def cleanup_expired(self, days, dry_run):
                raise self.MemoryStoreError("Supabase request failed (HTTP 500)")

            def prune_quota(self, dry_run):
                raise AssertionError("must not prune after a failed cleanup")

        with patch.object(self.admin, "_memory", Outage()):
            response = self.post({"dry_run": False})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Supabase", response.text)
        self.assertEqual(response.json()["detail"], "配额存储暂时不可用")

    def test_route_is_admin_gated_end_to_end(self):
        """Without the override the same route refuses anonymous and accepts a live session."""
        encoded = hash_password("x", iterations=1000)
        with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": "", "ADMIN_SESSION_SECRET": ""}):
            self.assertEqual(self.post({}, client=self.build(override=True)).status_code, 200)
            self.assertEqual(self.post({}, client=self.build(override=False)).status_code, 503)
            self.assertEqual(self.calls, [("cleanup", 90, True), ("prune", True)],
                             "管理员未配置时必须在进存储前就拒绝")
        self.calls.clear()
        with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": encoded, "ADMIN_SESSION_SECRET": SECRET}):
            gated = self.build(override=False)
            for token in (None, "", "forged"):
                self.assertEqual(self.post({}, token=token, client=gated).status_code, 401)
            stale = issue_session(SECRET, ttl=1, now=time.time() - 10)
            self.assertEqual(self.post({}, token=stale, client=gated).status_code, 401)
            self.assertEqual(self.calls, [], "未通过的令牌不能触发任何删除动作")
            self.assertEqual(self.post({}, token=issue_session(SECRET, ttl=600), client=gated).status_code, 200)
        self.assertEqual(self.calls, [("cleanup", 90, True), ("prune", True)],
                         "只有持有效会话令牌的管理员请求才会落到存储，且默认干跑")

    def test_require_admin_directly(self):
        from fastapi import HTTPException

        with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": "", "ADMIN_SESSION_SECRET": ""}):
            with self.assertRaises(HTTPException) as ctx:
                self.admin.require_admin(authorization=None)
        self.assertEqual(ctx.exception.status_code, 503)
        with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": hash_password("x", iterations=1000),
                                     "ADMIN_SESSION_SECRET": SECRET}):
            for header in (None, "", "Basic abc", "Bearer ", "Bearer nope"):
                with self.subTest(header=header):
                    with self.assertRaises(HTTPException) as ctx:
                        self.admin.require_admin(authorization=header)
                    self.assertEqual(ctx.exception.status_code, 401)
            self.admin.require_admin(authorization="Bearer " + issue_session(SECRET, ttl=600))


if __name__ == "__main__":
    unittest.main()

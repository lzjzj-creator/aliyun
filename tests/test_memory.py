"""Offline memory contract tests. All I/O is httpx.MockTransport; no services."""
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import os
import threading
import unittest
from unittest.mock import patch

import httpx

# Load with dummy configuration, never the developer's service credentials.
with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
}):
    memory = importlib.import_module("backend.memory")

SID = "12345678-1234-4234-8234-123456789abc"
OTHER = "87654321-4321-4321-8321-cba987654321"


class Store:
    """Small REST model for testing requests/CAS, not a PostgreSQL emulator."""
    def __init__(self):
        self.sessions = {}
        self.messages = []
        self.profiles = {}
        self.requests = []
        self.lock = threading.Lock()
        self.cas_barrier = None
        self.requested_page_size = 500

    @staticmethod
    def filtered(rows, params):
        for field, value in params.items():
            if value.startswith("eq."):
                rows = [r for r in rows if str(r.get(field)) == value[3:]]
        return rows

    def __call__(self, request):
        # Let two profile readers see exactly the same version before CAS.
        if request.method == "GET" and request.url.path.endswith("/user_profiles") and self.cas_barrier:
            with self.lock:
                snapshot = self.handle(request)
            self.cas_barrier.wait(timeout=5)
            return snapshot
        with self.lock:
            return self.handle(request)

    def handle(self, request):
        self.requests.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        data = json.loads(request.content) if request.content else {}
        if table == "cleanup_expired_sessions":
            return httpx.Response(200, json=3)
        if table == "chat_sessions":
            rows = list(self.sessions.values())
        elif table == "chat_messages":
            rows = self.messages
        else:
            rows = list(self.profiles.values())
        matches = self.filtered(rows, params)
        if request.method == "GET":
            if table == "chat_messages":
                matches = sorted(matches, key=lambda row: row["id"])
            offset = int(params.get("offset", 0))
            limit = min(int(params.get("limit", 1000)), self.requested_page_size)
            return httpx.Response(200, json=copy.deepcopy(matches[offset:offset + limit]))
        if request.method == "POST" and table == "chat_sessions":
            row = {"id": SID, "title": "新会话", "stage": "greeting", **data}
            self.sessions[SID] = row
            self.profiles[SID] = {"session_id": SID, "profile": {}, "coverage": 0,
                                  "coverage_detail": {}, "version": 0}
            return httpx.Response(201, json=[copy.deepcopy(row)])
        if request.method == "POST" and table == "chat_messages":
            if data["role"] == "user" and data.get("client_msg_id") is not None:
                for old in self.messages:
                    if (old["role"] == "user" and old["session_id"] == data["session_id"]
                            and old.get("client_msg_id") == data["client_msg_id"]):
                        return httpx.Response(409, json={"code": "23505"})
            row = {"id": len(self.messages) + 1, **data}
            self.messages.append(row)
            return httpx.Response(201, json=[copy.deepcopy(row)])
        if request.method == "PATCH":
            for row in matches:
                row.update(copy.deepcopy(data))
            return httpx.Response(200, json=copy.deepcopy(matches))
        if request.method == "DELETE":
            for row in matches:
                sid = row["id"]
                del self.sessions[sid]
                self.profiles.pop(sid, None)
                self.messages[:] = [m for m in self.messages if m["session_id"] != sid]
            return httpx.Response(200, json=copy.deepcopy(matches))
        raise AssertionError("Unexpected request")


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.client = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            headers={"apikey": "test-service-key", "Authorization": "Bearer test-service-key"},
            transport=httpx.MockTransport(self.store),
        )
        self.client_patch = patch.object(memory, "_client", self.client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.addCleanup(self.client.close)
        self.base_patch = patch.object(memory, "KB_SOURCE_BASE", "https://mirror.invalid")
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)

    def create(self):
        return memory.create_session("anon-1")

    def test_creation_hashes_random_32_bytes_returns_token_once(self):
        result = self.create()
        token = result["token"]
        self.assertEqual(len(base64.urlsafe_b64decode(token + "=")), 32)
        payload = json.loads(self.store.requests[0].content)
        self.assertEqual(payload["session_token_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, self.store.requests[0].content.decode())
        self.assertNotIn("session_token_hash", result["session"])
        self.assertNotIn("token", payload)
        self.assertEqual(self.store.profiles[SID]["version"], 0)
        self.assertEqual(self.store.requests[0].headers["apikey"], "test-service-key")
        self.assertEqual(self.store.requests[0].headers["Authorization"], "Bearer test-service-key")

    def test_verify_ownership_constant_time_and_unknown_session(self):
        token = self.create()["token"]
        original = memory.hmac.compare_digest
        with patch.object(memory.hmac, "compare_digest", wraps=original) as compare:
            self.assertTrue(memory.verify_session(SID, "anon-1", token))
            self.assertFalse(memory.verify_session(SID, "anon-2", token))
            self.assertFalse(memory.verify_session(SID, "anon-1", "forged"))
            self.assertFalse(memory.verify_session(OTHER, "anon-1", token))
            self.assertEqual(compare.call_count, 4)
        self.assertFalse(memory.verify_session("not-a-uuid", "anon-1", token))
        self.assertFalse(memory.verify_session(SID, "anon-1", None))

    def test_sessions_list_and_bundle_do_not_expose_hash(self):
        self.create()
        memory.append_message(SID, "user", "hello", client_msg_id="one")
        listed = memory.list_sessions("anon-1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(memory.list_sessions("anon-2"), [])
        bundle = memory.get_session_bundle(SID)
        self.assertEqual(len(bundle["messages"]), 1)
        self.assertEqual(bundle["profile"]["coverage"], 0)
        self.assertNotIn("session_token_hash", bundle["session"])
        self.assertNotIn("session_token_hash", listed[0])
        self.assertIsNone(memory.get_session_bundle(OTHER))

    def test_bundle_paginates_even_when_server_caps_page_below_requested(self):
        self.create()
        self.store.requested_page_size = 2
        for i in range(5):
            memory.append_message(SID, "user", str(i))
        self.assertEqual([m["id"] for m in memory.get_session_bundle(SID)["messages"]], [1, 2, 3, 4, 5])
        gets = [r for r in self.store.requests if r.method == "GET" and r.url.path.endswith("/chat_messages")]
        self.assertEqual([r.url.params["offset"] for r in gets], ["0", "2", "4", "5"])

    def test_message_idempotency_keeps_original_and_does_not_dedupe_assistant(self):
        self.create()
        first = memory.append_message(SID, "user", "original", client_msg_id="key")
        again = memory.append_message(SID, "user", "different retry", client_msg_id="key")
        self.assertEqual(first, again)
        for _ in range(2):
            memory.append_message(SID, "assistant", "reply", client_msg_id="key")
        self.assertEqual(len(self.store.messages), 3)
        self.assertEqual(self.store.messages[0]["content"], "original")

    def test_concurrent_duplicate_user_message_inserts_only_once(self):
        self.create()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: memory.append_message(SID, "user", "x", client_msg_id="same"), range(2)))
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(len(self.store.messages), 1)

    def test_sources_mirrored_before_insert_and_update_without_mutating_input(self):
        self.create()
        sources = [{"url": "https://upstream.invalid/a/", "source_url": "https://upstream.invalid/b/"}]
        before = copy.deepcopy(sources)
        row = memory.append_message(SID, "assistant", "", status="streaming", sources=sources)
        self.assertEqual(row["sources"][0]["url"], "https://mirror.invalid/a/index.html")
        self.assertEqual(row["sources"][0]["source_url"], "https://mirror.invalid/b/index.html")
        row = memory.update_message_status(row["id"], "done", content="answer", steps=[{"id": "step"}], sources=sources)
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["content"], "answer")
        self.assertEqual(sources, before)
        self.assertEqual(memory.update_message_status(row["id"], "error")["status"], "error")

    def test_profile_deep_merge_coverage_and_stale_version(self):
        self.create()
        first = memory.merge_profile(SID, {"identity": {"school": "X", "major": "CS"}, "skills": ["Python"]}, 0, coverage=0.25)
        second = memory.merge_profile(SID, {"identity": {"major": "Math"}, "skills": ["SQL"]}, 1, coverage_detail={"identity": 1})
        self.assertEqual(second["profile"]["identity"], {"school": "X", "major": "Math"})
        self.assertEqual(second["profile"]["skills"], ["SQL"])
        self.assertEqual(second["coverage"], 0.25)
        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        with self.assertRaises(memory.ProfileConflict):
            memory.merge_profile(SID, {"lost": True}, 0)
        self.assertNotIn("lost", memory.get_profile(SID)["profile"])

    def test_concurrent_profile_merge_compare_and_swap_only_one_wins(self):
        self.create()
        self.store.cas_barrier = threading.Barrier(2)

        def writer(key):
            try:
                return memory.merge_profile(SID, {key: True}, 0)
            except memory.ProfileConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, ["left", "right"]))
        self.store.cas_barrier = None
        self.assertEqual(sum(r == "conflict" for r in results), 1)
        stored = memory.get_profile(SID)
        self.assertEqual(stored["version"], 1)
        self.assertEqual(len(stored["profile"]), 1)

    def test_title_never_replaces_existing_title_or_uses_later_message(self):
        self.create()
        message = "这是一段用于验证首条用户消息二十个字符截断的标题内容"
        memory.append_message(SID, "user", message)
        row = memory.touch_title(SID, message)
        self.assertEqual(row["title"], message[:20])
        self.assertNotIn("session_token_hash", row)
        self.assertIsNone(memory.touch_title(SID, message))
        self.assertIsNone(memory.touch_title(SID, "not first"))

    def test_stage_delete_and_missing_entities(self):
        self.create()
        self.assertEqual(memory.set_session_stage(SID, "probing")["stage"], "probing")
        self.assertNotIn("session_token_hash", memory.set_session_stage(SID, "done"))
        memory.append_message(SID, "user", "hello")
        self.assertTrue(memory.delete_session(SID))
        self.assertFalse(memory.delete_session(SID))
        self.assertEqual(self.store.messages, [])
        self.assertIsNone(memory.get_profile(SID))
        with self.assertRaises(memory.SessionNotFound):
            memory.merge_profile(SID, {}, 0)
        with self.assertRaises(memory.SessionNotFound):
            memory.update_message_status(999, "done")

    def test_cleanup_dry_run_is_default_and_retention_guarded(self):
        self.assertEqual(memory.cleanup_expired(), 3)
        self.assertEqual(json.loads(self.store.requests[-1].content), {"days": 90, "dry_run": True})
        memory.cleanup_expired(100, False)
        self.assertEqual(json.loads(self.store.requests[-1].content), {"days": 100, "dry_run": False})
        for days, dry_run in [(89, False), (True, True), (90, None)]:
            with self.assertRaises(ValueError):
                memory.cleanup_expired(days, dry_run)

    def test_errors_never_include_request_body_or_supabase_response(self):
        def failure(request):
            return httpx.Response(500, json={"message": "secret raw message and token"})
        with httpx.Client(base_url="https://supabase.invalid/rest/v1", transport=httpx.MockTransport(failure)) as client:
            with patch.object(memory, "_client", client):
                with self.assertRaises(memory.MemoryStoreError) as context:
                    memory.append_message(SID, "user", "private user body")
        self.assertNotIn("secret", str(context.exception))
        self.assertNotIn("private", str(context.exception))

    def test_input_validation_rejects_before_network(self):
        calls = [
            lambda: memory.create_session(""),
            lambda: memory.list_sessions("anon", 0),
            lambda: memory.append_message(SID, "hacker", "x"),
            lambda: memory.append_message(SID, "user", None),
            lambda: memory.append_message(SID, "user", "x", client_msg_id=""),
            lambda: memory.append_message(SID, "user", "x", steps={}),
            lambda: memory.append_message(SID, "user", "x", meta=[]),
            lambda: memory.merge_profile(SID, {}, 0, coverage=float("nan")),
            lambda: memory.merge_profile(SID, {}, 0, coverage=1.1),
            lambda: memory.merge_profile(SID, {}, -1),
            lambda: memory.set_session_stage(SID, "invalid"),
        ]
        for call in calls:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(self.store.requests, [])


if __name__ == "__main__":
    unittest.main()

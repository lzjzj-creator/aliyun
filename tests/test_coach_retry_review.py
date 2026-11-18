"""Offline regression tests for coach retry identity and stream finalization."""
import asyncio
import importlib
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
    "COACH_ENABLED": "1",
}):
    from backend import coach, memory, session_security as security
    route = importlib.import_module("backend.routers.coach")
from tests.test_memory import Store, SID

ANON = "11111111-1111-4111-8111-111111111111"


def parse(body):
    import json
    return [(block.split("event: ", 1)[1].split("\n", 1)[0],
             json.loads(block.split("\ndata: ", 1)[1]))
            for block in body.split("\n\n") if block.strip()]


class CoachRetryReviewTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        transport = httpx.Client(base_url="https://supabase.invalid/rest/v1",
                                 transport=httpx.MockTransport(self.store))
        self.addCleanup(transport.close)
        self.limiter = security.RateLimiter()
        patches = [patch.object(memory, "_client", transport),
                   patch.object(security, "limiter", self.limiter),
                   patch.object(coach, "budget_ok", return_value=True),
                   patch.dict(os.environ, {"COACH_ENABLED": "1"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.token = memory.create_session(ANON)["token"]
        app = FastAPI()
        app.include_router(route.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def post(self, key="A", text="question"):
        return self.client.post("/api/v2/chat/stream",
                                json={"session_id": SID, "client_msg_id": key,
                                      "message": text},
                                headers={"X-Anon-Id": ANON,
                                         "X-Session-Token": self.token})

    def response(self, key="A", text="question"):
        return route.chat_stream(
            route.ChatReq(session_id=SID, client_msg_id=key, message=text),
            Request({"type": "http", "headers": []}), ANON, self.token)

    def assertReleased(self):
        self.assertEqual(self.limiter._streams, set())

    def test_failed_partial_retry_does_not_charge_or_append_user_twice(self):
        def fail(*args):
            yield "step_start", {"id": "s", "kind": "answer", "seq": 1}
            yield "reasoning_summary", "summary"
            yield "token", "partial"
            raise RuntimeError("private upstream error")
        with patch.object(security, "charge_turn", wraps=security.charge_turn) as charge:
            with patch.object(coach, "turn_events", side_effect=fail):
                failed = parse(self.post().text)
            self.assertEqual(failed[-1][0], "error")
            self.assertNotIn("done", dict(failed))
            row = self.store.messages[-1]
            self.assertEqual((row["status"], row["content"], row["client_msg_id"]),
                             ("error", "partial", "A"))
            self.assertEqual(row["steps"][0]["id"], "s")
            self.assertEqual(row["meta"]["reasoning"], "summary")
            with patch.object(coach, "turn_events", return_value=iter([("token", "success")])) as run:
                retried = parse(self.post().text)
            self.assertEqual(charge.call_count, 1)
            self.assertEqual([m["role"] for m in self.store.messages].count("user"), 1)
            self.assertEqual(retried[-1][0], "done")
            self.assertEqual(self.store.messages[-1]["content"], "success")
            history = run.call_args.args[2]
            self.assertFalse(any(m.get("status") == "error" for m in history))
        self.assertReleased()

    def test_A_B_A_replays_only_A_with_frontend_events(self):
        def answer(session, profile, history, text):
            yield "sources", [{"url": "https://mirror.invalid/a"}]
            yield "reasoning_summary", text + " summary"
            yield "token", text + " answer"
            yield "card", {"type": "suggest_reply", "payload": {}}
            yield "profile", {"coverage": 0.25 if text == "alpha" else 0.75}
        with patch.object(coach, "turn_events", side_effect=answer) as run, \
             patch.object(security, "charge_turn", wraps=security.charge_turn) as charge:
            a = parse(self.post("A", "alpha").text)
            b = parse(self.post("B", "beta").text)
            replay = parse(self.post("A", "alpha").text)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(charge.call_count, 2)
        self.assertEqual(dict(replay)["token"], "alpha answer")
        self.assertEqual(dict(replay)["done"], dict(a)["done"])
        self.assertNotEqual(dict(replay)["done"]["message_id"], dict(b)["done"]["message_id"])
        self.assertNotIn("error", dict(replay))
        self.assertIn("card", dict(replay))
        self.assertIn("sources", dict(replay))
        self.assertEqual(len(self.store.messages), 4)
        self.assertReleased()

    def test_same_key_different_text_conflicts_before_sse_and_quota(self):
        memory.append_message(SID, "user", "original", client_msg_id="A")
        with patch.object(security, "charge_turn") as charge, \
             patch.object(coach, "turn_events") as run:
            res = self.post("A", "different")
        self.assertEqual(res.status_code, 409)
        self.assertIn("application/json", res.headers["content-type"])
        charge.assert_not_called()
        run.assert_not_called()
        self.assertReleased()

    def test_missing_assistant_retries_without_charge(self):
        memory.append_message(SID, "user", "question", client_msg_id="A")
        with patch.object(security, "charge_turn") as charge, \
             patch.object(coach, "turn_events", return_value=iter([("token", "answer")])):
            events = parse(self.post().text)
        charge.assert_not_called()
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(len(self.store.messages), 2)
        self.assertEqual(self.store.messages[-1]["client_msg_id"], "A")
        self.assertReleased()

    def test_streaming_is_explicit_in_progress_not_done(self):
        memory.append_message(SID, "user", "question", client_msg_id="A")
        memory.append_message(SID, "assistant", "partial", client_msg_id="A", status="streaming")
        with patch.object(coach, "turn_events") as run:
            events = parse(self.post().text)
        self.assertEqual(dict(events)["error"]["code"], "IN_PROGRESS")
        self.assertNotIn("done", dict(events))
        self.assertNotIn("token", dict(events))
        run.assert_not_called()
        self.assertReleased()

    def test_legacy_unkeyed_assistant_never_binds_to_last_result(self):
        memory.append_message(SID, "user", "question", client_msg_id="A")
        memory.append_message(SID, "assistant", "legacy unrelated")
        with patch.object(coach, "turn_events") as run:
            res = self.post()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(len(self.store.messages), 2)
        run.assert_not_called()
        self.assertReleased()

    def test_generator_close_at_session_releases_lease(self):
        response = self.response()
        self.assertIn("event: session", next(response._iterator))
        response._iterator.close()
        self.assertReleased()

    def test_generator_cancel_preserves_partial_and_closes_upstream(self):
        closed = []
        def events(*args):
            try:
                yield "token", "partial"
                yield "token", "not delivered"
            finally:
                closed.append(True)
        with patch.object(coach, "turn_events", side_effect=events):
            response = self.response()
            next(response._iterator)
            next(response._iterator)
            response._iterator.close()
        self.assertEqual(closed, [True])
        self.assertEqual(self.store.messages[-1]["status"], "error")
        self.assertEqual(self.store.messages[-1]["content"], "partial")
        self.assertReleased()

    def test_send_failure_before_generator_starts_releases_lease(self):
        response = self.response()
        async def send(message):
            raise OSError("connection closed")
        async def receive():
            return {"type": "http.disconnect"}
        with self.assertRaises(ClientDisconnect):
            asyncio.run(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send))
        self.assertReleased()

    def test_same_key_empty_text_is_also_conflict(self):
        memory.append_message(SID, "user", "question", client_msg_id="A")
        self.assertEqual(self.post("A", "   ").status_code, 409)
        self.assertReleased()

    def test_asgi_disconnect_after_token_persists_partial(self):
        closed = []
        def events(*args):
            try:
                yield "token", "partial"
                yield "token", "not delivered"
            finally:
                closed.append(True)
        async def send(message):
            if message.get("type") == "http.response.body" and b"event: token" in message.get("body", b""):
                raise OSError("connection closed")
        async def receive():
            return {"type": "http.disconnect"}
        with patch.object(coach, "turn_events", side_effect=events):
            response = self.response()
            with self.assertRaises(ClientDisconnect):
                asyncio.run(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send))
        self.assertEqual(closed, [True])
        self.assertEqual(self.store.messages[-1]["status"], "error")
        self.assertEqual(self.store.messages[-1]["content"], "partial")
        self.assertReleased()

    def test_new_session_initialization_failure_compensates(self):
        with patch.object(memory, "get_session_bundle", side_effect=memory.MemoryStoreError):
            res = self.client.post("/api/v2/chat/stream", json={"message": ""},
                                   headers={"X-Anon-Id": ANON})
        self.assertEqual(res.status_code, 503)
        self.assertEqual(self.store.sessions, {})
        self.assertReleased()

    def test_new_greeting_failure_without_tokens_compensates(self):
        with patch.object(coach, "greeting_events", side_effect=RuntimeError):
            res = self.client.post("/api/v2/chat/stream", json={"message": ""},
                                   headers={"X-Anon-Id": ANON})
        self.assertEqual(parse(res.text)[-1][0], "error")
        self.assertEqual(self.store.sessions, {})
        self.assertReleased()


if __name__ == "__main__":
    unittest.main()

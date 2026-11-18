"""Offline HTTP-200 SSE regressions; every request uses httpx.MockTransport."""
import json
import traceback
import unittest
from unittest.mock import patch

import httpx

from backend.llm import LLM

CANARY = "sk-CANARY-upstream-secret-and-request-echo"
ERROR = "LLM stream failed"


def frame(content=None, finish_reason=None, **delta):
    if content is not None:
        delta["content"] = content
    return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}


def sse(*frames):
    return "".join("data: " + (f if isinstance(f, str) else json.dumps(f)) + "\n\n"
                   for f in frames)


class LLMStreamReviewTest(unittest.TestCase):
    def setUp(self):
        # A stray real HTTP/database connection must fail instead of leaving the host.
        guard = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        # Stream-contract tests must not depend on ambient v2 flags captured at
        # import time (other suites enable COACH_ENABLED for their own imports).
        from backend import model_budget
        budget = patch.object(model_budget, "ENABLED", False)
        budget.start()
        self.addCleanup(budget.stop)

    def stream(self, body, status=200):
        def respond(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/compatible-mode/v1/chat/completions")
            self.assertTrue(json.loads(request.content)["stream"])
            return httpx.Response(status, text=body,
                                  headers={"Content-Type": "text/event-stream"})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        llm = LLM.__new__(LLM)
        llm._client = client
        return llm.chat_stream([{"role": "user", "content": "test"}])

    def assert_failure(self, body, partial=True, status=200):
        stream = self.stream(body, status)
        if partial:
            self.assertEqual(next(stream), "partial")
        with self.assertRaises(RuntimeError) as caught:
            list(stream)
        self.assertEqual(str(caught.exception), ERROR)
        self.assertNotIn(CANARY, repr(caught.exception))
        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn(CANARY, rendered)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_normal_delta_and_done(self):
        body = ": keepalive\n\nevent: message\n" + sse(
            frame(role="assistant"), frame("你好"), frame("！"), "[DONE]")
        self.assertEqual(list(self.stream(body)), ["你好", "！"])

    def test_stop_is_success_with_or_without_done(self):
        for trailer in ((), ("[DONE]",)):
            with self.subTest(trailer=trailer):
                self.assertEqual(list(self.stream(sse(
                    frame("partial"), frame(finish_reason="stop"), *trailer))), ["partial"])

    def test_final_content_with_stop_and_no_delta_terminal(self):
        self.assertEqual(list(self.stream(sse(frame("final", finish_reason="stop")))),
                         ["final"])
        self.assertEqual(list(self.stream(sse({"choices": [{"finish_reason": "stop"}]}))), [])

    def test_usage_empty_choices_is_valid_but_not_terminal(self):
        usage = {"choices": [], "usage": {"total_tokens": 7}}
        for body in (sse(frame("partial"), usage, "[DONE]"),
                     sse(frame("partial"), frame(finish_reason="stop"), usage)):
            with self.subTest(body=body):
                self.assertEqual(list(self.stream(body)), ["partial"])
        self.assert_failure(sse(frame("partial"), usage))

    def test_reasoning_content_does_not_leave_server(self):
        self.assertEqual(list(self.stream(sse(
            frame(reasoning_content=CANARY), frame("answer"), "[DONE]"))), ["answer"])

    def test_error_frame_after_partial_is_not_success(self):
        for error in ({"message": CANARY}, CANARY, None):
            with self.subTest(error=error):
                self.assert_failure(sse(frame("partial"), {"error": error}, "[DONE]"))

    def test_malformed_json_and_choices_after_partial(self):
        bad_frames = [
            "{" + CANARY, [], None, {"detail": CANARY},
            {"choices": None}, {"choices": {}}, {"choices": []},
            {"choices": [], "usage": CANARY}, {"choices": [None]},
            {"choices": [{"delta": CANARY}]}, {"choices": [{}]},
            {"choices": [{"delta": {"content": {"secret": CANARY}}}]},
            {"choices": [{"delta": {"content": [CANARY]}}]},
            {"choices": [{"delta": {}}, {"delta": CANARY}]},
        ]
        for bad in bad_frames:
            with self.subTest(bad=bad):
                self.assert_failure(sse(frame("partial"), bad, "[DONE]"))

    def test_eof_requires_explicit_success(self):
        self.assert_failure(sse(frame("partial")))
        self.assert_failure("", partial=False)
        self.assert_failure(sse(frame(role="assistant")), partial=False)

    def test_unsuccessful_finish_reason_even_before_done(self):
        for reason in ("length", "content_filter", "tool_calls", "function_call", CANARY, 1, {}):
            for trailer in ((), ("[DONE]",)):
                with self.subTest(reason=reason, trailer=trailer):
                    self.assert_failure(sse(frame("partial"),
                                            frame(finish_reason=reason), *trailer))

    def test_error_after_stop_is_not_hidden(self):
        self.assert_failure(sse(frame("partial"), frame(finish_reason="stop"),
                                {"error": {"message": CANARY}}, "[DONE]"))

    def test_content_after_stop_is_invalid(self):
        self.assert_failure(sse(frame("partial"), frame(finish_reason="stop"),
                                frame(CANARY), "[DONE]"))

    def test_http_status_and_transport_errors_are_sanitized(self):
        self.assert_failure(CANARY, partial=False, status=500)
        def fail(request):
            raise httpx.ReadError(CANARY, request=request)
        client = httpx.Client(transport=httpx.MockTransport(fail))
        self.addCleanup(client.close)
        llm = LLM.__new__(LLM)
        llm._client = client
        with self.assertRaisesRegex(RuntimeError, "^" + ERROR + "$") as caught:
            list(llm.chat_stream([]))
        self.assertNotIn(CANARY, "".join(traceback.format_exception(caught.exception)))


if __name__ == "__main__":
    unittest.main()

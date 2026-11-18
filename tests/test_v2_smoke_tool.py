"""离线校验 tests/v2_smoke.py 自身的解析逻辑（SSE 帧、泄漏扫描、p95 口径）。

冒烟脚本本身要打真实部署，这里只保证它对协议的解读是对的：
帧名取自 `event:` 行、令牌只在 session 帧、p95 在 <30 样本时不成立。
"""
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v2_smoke", ROOT / "tests" / "v2_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class FakeStream:
    """最简 httpx 流式响应替身：只实现 iter_lines()。"""
    def __init__(self, frames):
        self.frames = frames

    def iter_lines(self):
        for name, data in self.frames:
            yield f"event: {name}"
            yield f"data: {json.dumps(data, ensure_ascii=False)}"
            yield ""


class StreamParseTest(unittest.TestCase):
    def parse(self, frames):
        return smoke.read_stream(FakeStream(frames), [])

    def test_event_names_come_from_event_line(self):
        result = self.parse([("session", {"protocol": 2, "session_id": "s", "stage": "greeting",
                                          "is_new": True, "session_token": "tok"}),
                             ("step_start", {"id": "retrieve"}),
                             ("reasoning_summary", {"text": "已检索到 3 篇"}),
                             ("done", {"session_id": "s", "stage": "probing", "coverage": 0.2})])
        self.assertEqual(result["names"], ["session", "step_start", "reasoning_summary", "done"])
        self.assertEqual(result["session"]["session_token"], "tok")
        self.assertTrue(result["done"])
        self.assertFalse(result["error"])
        self.assertIsNotNone(result["first_step"])
        self.assertIsNotNone(result["first_token"])
        self.assertLessEqual(result["first_step"], result["first_token"])

    def test_error_frame_is_flagged(self):
        result = self.parse([("session", {"session_id": "s"}),
                             ("error", {"code": "UPSTREAM", "message": "模型超时", "retryable": True})])
        self.assertTrue(result["error"])
        self.assertFalse(result["done"])

    def test_malformed_json_is_reported_not_raised(self):
        findings = []

        class Broken:
            @staticmethod
            def iter_lines():
                yield "event: token"
                yield "data: {not json"

        smoke.read_stream(Broken(), findings)
        self.assertTrue(any("不是合法 JSON" in item for item in findings))


class LeakScanTest(unittest.TestCase):
    def scan(self, payload):
        findings = []
        smoke.scan(payload, "probe", findings)
        return findings

    def test_detects_hash_key_and_upstream_markers(self):
        self.assertTrue(self.scan({"session": {"session_token_hash": "a" * 64}}))
        self.assertTrue(self.scan({"note": "Bearer eyJhbGciOiJIUzI1NiJ9"}))
        self.assertTrue(self.scan({"raw": "role: service_role"}))
        self.assertTrue(self.scan([{"k": {"apikey": "x"}}]))

    def test_token_itself_is_not_a_leak_signal(self):
        # 一次性会话令牌必须允许出现（首帧下发），否则冒烟脚本会把自己判成泄漏
        self.assertEqual(self.scan({"session": {"session_token": "Zm9v"}}), [])
        self.assertEqual(self.scan({"sources": [{"url": "https://mirror.invalid/a/index.html"}]}), [])


class SampleRuleTest(unittest.TestCase):
    def summary(self, values):
        timings = {"首 step": [], "首 token": [], "整轮": list(values)}
        args = type("A", (), {"json_out": "", "base": "https://x.invalid"})()
        buffer, checks = io.StringIO(), [{"name": "x", "ok": True, "detail": ""}]
        with patch("sys.stdout", buffer):
            smoke.finish(checks, timings, [], args)
        return buffer.getvalue()

    def test_below_30_samples_reports_no_p95(self):
        out = self.summary([1.0] * 29)
        self.assertIn("p95 不成立", out)

    def test_at_least_30_samples_reports_p95(self):
        out = self.summary([float(i % 5) for i in range(30)])
        self.assertNotIn("p95 不成立", out)
        self.assertIn("'p95': 4.0", out)


if __name__ == "__main__":
    unittest.main()

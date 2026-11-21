"""§13「隐私」行的离线证据：后端不打日志、错误响应不回显上游原文、正文不进 URL。

FC 的访问日志由 uvicorn 访问日志承担，只记录请求行（方法 + 路径 + 查询串）与状态码。
因此三件事必须成立，且都要能被脚本复核：
  1. 后端代码里没有任何自行打日志/打印的出口（正文与令牌不会被写进日志）；
  2. 任何上游异常原文（可能含密钥片段或请求体回声）都不进 HTTP/SSE 响应；
  3. 消息正文、令牌、匿名 ID 都不出现在查询串里（否则会被访问日志留痕）。
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
FORBIDDEN_LOG_CALLS = ("logging", "logger.", "traceback", "pprint", "print(", "sys.stderr")
CANARY = "sk-CANARY-upstream-echo-of-request-body"

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
    "DASHSCOPE_API_KEY": "test-only",
    "V2_MEMORY_ENABLED": "1",
    "COACH_ENABLED": "1",
}):
    from backend import main, memory
    from backend.routers import sessions as sessions_router
from tests.test_memory import Store

ANON = "11111111-1111-4111-8111-111111111111"


def backend_sources():
    for path in sorted(BACKEND.rglob("*.py")):
        if "vendor" in path.parts or "__pycache__" in path.parts:
            continue
        yield path, path.read_text(encoding="utf-8")


class NoLoggingTest(unittest.TestCase):
    def test_backend_has_no_log_or_print_sinks(self):
        offenders = [(path.relative_to(ROOT), token)
                     for path, text in backend_sources() for token in FORBIDDEN_LOG_CALLS if token in text]
        self.assertEqual(offenders, [], f"后端出现日志/打印出口：{offenders}")

    def test_no_exception_text_reaches_a_response(self):
        """异常原文只允许进我们自己的日志（现在没有日志），不允许拼进 detail/message。"""
        banned = ("detail=str(", "detail=f\"{", '"message": str(', '"message": f"{e', "str(exc)")
        offenders = [(path.relative_to(ROOT), token)
                     for path, text in backend_sources() for token in banned if token in text]
        self.assertEqual(offenders, [], f"响应里回显了异常原文：{offenders}")

    def test_server_entry_only_boots_uvicorn(self):
        text = (BACKEND / "index.py").read_text(encoding="utf-8")
        self.assertIn("uvicorn.run", text)
        self.assertNotIn("log_config", text)   # 默认访问日志：只记请求行，不记头与体


class QueryStringHygieneTest(unittest.TestCase):
    """正文/令牌不进 URL：memory 的 params 只允许出现白名单过滤字段。"""
    ALLOWED_PARAMS = {"select", "order", "limit", "offset", "id", "session_id", "anon_id",
                      "role", "client_msg_id", "version", "title"}

    def params_keys(self):
        text = (BACKEND / "memory.py").read_text(encoding="utf-8")
        keys = set(re.findall(r'"([a-z_]+)":\s*f?"', text))
        return {key for key in keys if key not in {"eq."}}

    def test_only_allowlisted_filter_fields_reach_the_url(self):
        unexpected = self.params_keys() - self.ALLOWED_PARAMS
        self.assertEqual(unexpected, set(), f"意外进入查询串的字段：{unexpected}")

    def test_rpc_payloads_are_json_bodies_not_urls(self):
        text = (BACKEND / "memory.py").read_text(encoding="utf-8")
        for call in re.findall(r'_request\("POST", "(/rpc/[a-z_]+)"[^)]*\)', text, flags=re.S):
            self.assertIn(call, {"/rpc/cleanup_expired_sessions", "/rpc/v2_spend_quota",
                                 "/rpc/v2_acquire_stream_lease", "/rpc/v2_release_stream_lease",
                                 "/rpc/v2_charge_tokens", "/rpc/v2_token_usage_today",
                                 "/rpc/v2_prune_quota"})
        self.assertNotIn('params={"p_', text)   # 配额参数走 JSON 体，不进查询串


class V3HygieneTest(unittest.TestCase):
    """#75 v3 专项：验证物/正文/证据不进 URL，令牌只走 Header，错误不回显。"""

    # URL 里只允许出现标识/过滤键，绝不允许验证物与内容载荷
    BANNED_URL_KEYS = {"owner_key_hash", "recovery_hash", "value", "quote",
                       "evidence", "content", "token", "owner_token",
                       "recovery_code", "fields", "snapshot"}

    def test_v3_store_urls_carry_only_filter_keys(self):
        text = (BACKEND / "memory_v3.py").read_text(encoding="utf-8")
        keys = set(re.findall(r'"([a-z_]+)":\s*f?"', text))
        leaked = keys & self.BANNED_URL_KEYS
        self.assertEqual(leaked, set(), f"敏感字段进入 URL：{leaked}")

    def test_owner_lookup_hashes_go_through_rpc_body(self):
        """哈希查找必须走 /rpc/ JSON body——URL 会进访问日志。"""
        text = (BACKEND / "memory_v3.py").read_text(encoding="utf-8")
        self.assertNotRegex(text, r'params=\{[^}]*"owner_key_hash"')
        self.assertNotRegex(text, r'params=\{[^}]*"recovery_hash"')
        self.assertIn("/rpc/v3_find_owner", text)

    def test_owner_token_only_via_header(self):
        for name in ("memory_v3.py", "resumes_v3.py"):
            text = (BACKEND / "routers" / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"Query\(.*token", name)
            self.assertNotRegex(text, r"params=.*token", name)
            self.assertIn("x_owner_token", text)

    def test_v3_storage_failure_does_not_echo_upstream(self):
        """v3 存储 500 → 统一 503，上游原文与业务数据都不外泄。"""
        import importlib
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        rv3 = importlib.import_module("backend.routers.memory_v3")
        failing = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            transport=httpx.MockTransport(lambda request: httpx.Response(
                500, json={"message": f"upstream said {CANARY}"})))
        app = FastAPI()
        app.include_router(rv3.router)
        try:
            with patch.object(memory, "_client", failing), \
                    patch.object(rv3.security, "enforce_ip", lambda r: None), \
                    patch.object(rv3.security_v3, "require_owner",
                                 lambda t, **kw: {"id": ANON, "memory_enabled": True}):
                r = TestClient(app).get("/api/v3/memory",
                                        headers={"X-Owner-Token": "t" * 40})
        finally:
            failing.close()
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(CANARY, r.text)


class ResponseSanitizationTest(unittest.TestCase):
    """上游报错回声与用户正文都不能出现在响应里。"""

    def v1_stream_error(self):
        from fastapi.testclient import TestClient

        def boom(messages, model=None, **kwargs):
            raise RuntimeError(f"400 Client Error: {CANARY} body={messages[0]['content'][:40]}")

        route = {"action": "answer", "intent": "kb_qa", "kb_scope": "all", "clarify_question": ""}
        hits = [{"title": "T", "source_url": "https://mirror.invalid/a/index.html",
                 "seq": 1, "score": 0.9, "text": "资料正文"}]
        with patch.object(main.decision, "route", return_value=route), \
                patch.object(main.llm, "embed", return_value=[0.1]), \
                patch.object(main.kb, "search", return_value=hits), \
                patch.object(main.llm, "chat_stream", side_effect=boom):
            response = TestClient(main.app).post("/api/chat/stream", json={"query": "我的秘密问题"})
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_v1_model_failure_does_not_echo_upstream(self):
        body = self.v1_stream_error()
        self.assertIn('"code": "MODEL_FAILED"', body)
        self.assertNotIn(CANARY, body)
        self.assertNotIn("我的秘密问题", body)
        self.assertNotIn("资料正文", body)
        self.assertIn("event: done", body)      # 出错也要终结，不留悬挂流

    def test_v2_storage_failure_does_not_echo_request_or_response(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        healthy = httpx.Client(base_url="https://supabase.invalid/rest/v1",
                               transport=httpx.MockTransport(Store()))
        failing = httpx.Client(base_url="https://supabase.invalid/rest/v1",
                               transport=httpx.MockTransport(lambda request: httpx.Response(
                                   500, json={"message": f"upstream said {CANARY}"})))
        app = FastAPI()
        app.include_router(sessions_router.router)
        with patch.object(memory, "_client", healthy), \
                patch.object(memory, "KB_SOURCE_BASE", "https://mirror.invalid"):
            created = memory.create_session(ANON)
        headers = {"X-Anon-Id": ANON, "X-Session-Token": created["token"]}
        with patch.object(memory, "_client", failing):
            response = TestClient(app).post("/api/v2/messages", headers=headers, json={
                "session_id": created["session"]["id"], "client_msg_id": "k1",
                "user": "我的秘密问题", "assistant": "回答"})
        healthy.close()
        failing.close()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(CANARY, response.text)
        self.assertNotIn("我的秘密问题", response.text)


if __name__ == "__main__":
    unittest.main()

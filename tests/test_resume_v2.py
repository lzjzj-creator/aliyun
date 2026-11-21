"""v2 简历域单测：模板契约、优化守卫、autofill 证据规则（不调外部 API）。

与 §13 评测表对应：契约快照（前后端不漂移）、数字/claim 守卫、字段路径约定。
"""
import json
import unittest
from pathlib import Path

from backend.resume_template import (
    load, derive_slots, required_slots, adaptive_min_entries,
    empty_fields, iter_field_paths, valid_path_syntax, validate_fields,
)
from backend.resume_optimizer import (
    guard_fabricated_numbers, guard_new_claims, extract_placeholder,
)

FIXTURE = Path(__file__).resolve().parent.parent / "frontend" / "public" / "demo" / "v2"


class TemplateContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tpl = load()

    def test_contract_snapshot_matches_frontend_fixture(self):
        """前后端读同一份契约：fixture 快照与后端 JSON 逐字节一致。"""
        backend_raw = json.loads(
            (Path(__file__).resolve().parent.parent / "backend" / "resume_template.json")
            .read_text(encoding="utf-8"))
        fixture_raw = json.loads((FIXTURE / "template.json").read_text(encoding="utf-8"))
        self.assertEqual(backend_raw, fixture_raw)

    def test_sections_and_slots(self):
        self.assertEqual(
            [s.key for s in self.tpl.sections],
            ["basics", "education", "internships", "projects", "skills", "awards", "summary"])
        self.assertEqual(len(derive_slots(self.tpl)), 9)

    def test_adaptive_required(self):
        # 无实习画像：internships slot 不强制，projects 要求上浮到 3
        ids = [s.id for s in required_slots(self.tpl, {})]
        self.assertNotIn("internships", ids)
        self.assertIn("projects", ids)
        self.assertEqual(adaptive_min_entries(self.tpl, "projects", {}), 3)
        # 有实习：internships 变必填，projects 回落到 2
        prof = {"experiences": [{"type": "internship"}]}
        ids2 = [s.id for s in required_slots(self.tpl, prof)]
        self.assertIn("internships", ids2)
        self.assertEqual(adaptive_min_entries(self.tpl, "projects", prof), 2)

    def test_empty_fields_and_paths(self):
        fields = empty_fields(self.tpl)
        self.assertEqual(set(fields), {"basics", "education", "internships",
                                       "projects", "skills", "awards", "summary"})
        self.assertFalse(validate_fields(self.tpl, fields))
        self.assertTrue(valid_path_syntax("projects.0.bullets.1"))
        self.assertTrue(validate_fields(self.tpl, {"nope": 1}))  # 未知 section 报错

    def test_iter_paths_on_fixture(self):
        fields = json.loads((FIXTURE / "autofill.json").read_text(encoding="utf-8"))["fields"]
        paths = set(iter_field_paths(self.tpl, fields))
        self.assertIn("basics.target_role", paths)
        self.assertIn("projects.0.bullets.0", paths)
        self.assertIn("skills.groups.0.items.0", paths)


class GuardTest(unittest.TestCase):
    ORIG = "基于协同过滤实现课程推荐，Top-5 推荐命中率在自测集上达 62%"

    def test_number_guard_allows_original_numbers(self):
        d = guard_fabricated_numbers(
            {"rewritten": [{"original": self.ORIG,
                            "suggested": "实现协同过滤推荐，Top-5 命中率 62%（自测集）"}]},
            [self.ORIG], "")
        self.assertFalse(d["rewritten"][0].get("needs_user_data"))

    def test_number_guard_blocks_fabricated(self):
        d = guard_fabricated_numbers(
            {"rewritten": [{"original": self.ORIG,
                            "suggested": "命中率提升 85%，Top-5 达 62%"}]},
            [self.ORIG], "")
        self.assertTrue(d["rewritten"][0]["needs_user_data"])
        self.assertTrue(d["questions"])

    def test_claim_guard_ignores_original_tokens(self):
        # Top-5 这种含数字的技术 token 在原文中，不得误报（回归：数字剥离曾误伤）
        d = guard_new_claims(
            {"rewritten": [{"original": self.ORIG,
                            "suggested": "实现基于协同过滤的课程推荐算法，Top-5 命中率 62%（自测集）"}]},
            [self.ORIG], "")
        self.assertFalse(d["rewritten"][0].get("needs_user_data"))

    def test_claim_guard_blocks_novel_tech(self):
        d = guard_new_claims(
            {"rewritten": [{"original": self.ORIG,
                            "suggested": "用 K8s 部署推荐服务，命中率 62%"}]},
            [self.ORIG], "")
        self.assertTrue(d["rewritten"][0]["needs_user_data"])

    def test_claim_guard_blocks_novel_cjk_enum(self):
        """顿号列举扩写（购物车/商品详情类）是纯中文编造，token 正则拦不住。"""
        orig = "用 Spring Boot 写了 12 个接口，主要是商品搜索和下单"
        d = guard_new_claims(
            {"rewritten": [{"original": orig,
                            "suggested": "实现商品搜索与下单全流程，覆盖用户端查询、商品详情、"
                                         "购物车、订单创建等关键链路"}]},
            [orig], "校园二手交易小程序 Spring Boot")
        self.assertTrue(d["rewritten"][0]["needs_user_data"])
        self.assertTrue(d["questions"])

    def test_claim_guard_allows_enum_in_corpus(self):
        """并列项都在语料里时不误报。"""
        orig = "用 Spring Boot 写了商品搜索和下单接口"
        d = guard_new_claims(
            {"rewritten": [{"original": orig,
                            "suggested": "支撑商品搜索、下单等核心链路"}]},
            [orig], "商品搜索 下单")
        self.assertFalse(d["rewritten"][0].get("needs_user_data"))

    def test_placeholder_extract(self):
        self.assertEqual(extract_placeholder("做了很多事【待补充：量化结果】"),
                         "【待补充：量化结果】")
        self.assertEqual(extract_placeholder("没有占位"), "")


class EndpointShapeTest(unittest.TestCase):
    """不调 LLM 的端点形状/校验测试（autofill 空对话、plan、409、400）。"""

    @classmethod
    def setUpClass(cls):
        # main.py 含 v1 平铺 import，不拉整个 app；只挂本域路由即可测形状
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        import backend.session_security as _sec
        from backend.routers.resume import router
        # .env 的 V2_SHARED_QUOTA=1 会让 SharedLimiter 打 live 存储；
        # 形状测试只需要进程内限流器，替换避免外部依赖。
        cls._limiter_patch = patch.object(_sec, "limiter", _sec.RateLimiter())
        cls._limiter_patch.start()
        cls.addClassCleanup(cls._limiter_patch.stop)
        app = FastAPI()
        app.include_router(router)
        cls.c = TestClient(app)
        cls.ver = cls.c.get("/api/v2/resume/template").json()["template_version"]

    H = {"X-Anon-Id": "11111111-1111-4111-8111-111111111111"}   # §5 三端点均需 anon

    def test_template_endpoint(self):
        r = self.c.get("/api/v2/resume/template")
        self.assertEqual(r.status_code, 200)
        self.assertIn("sections", r.json())

    def test_version_mismatch_409(self):
        r = self.c.post("/api/v2/resume/autofill", headers=self.H,
                        json={"template_version": "0.0.0", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 409)
        r = self.c.post("/api/v2/resume/optimize/plan", headers=self.H,
                        json={"template_version": "0.0.0", "fields": {}})
        self.assertEqual(r.status_code, 409)

    def test_autofill_requires_conversation(self):
        r = self.c.post("/api/v2/resume/autofill", headers=self.H,
                        json={"template_version": self.ver})
        self.assertEqual(r.status_code, 400)

    def test_autofill_empty_conversation_gaps(self):
        r = self.c.post("/api/v2/resume/autofill", headers=self.H,
                        json={"template_version": self.ver,
                              "messages": [{"role": "assistant", "content": "你好"}]})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["gaps"])

    def test_plan_shape_and_order(self):
        fields = json.loads((FIXTURE / "autofill.json").read_text(encoding="utf-8"))["fields"]
        r = self.c.post("/api/v2/resume/optimize/plan", headers=self.H,
                        json={"template_version": self.ver, "fields": fields})
        self.assertEqual(r.status_code, 200)
        secs = r.json()["sections"]
        prios = [s["priority"] for s in secs]
        self.assertEqual(prios, sorted(prios))
        by_key = {s["key"]: s for s in secs}
        self.assertEqual(by_key["projects"]["status"], "pending")
        self.assertEqual(by_key["internships"]["status"], "skipped")
        self.assertFalse(by_key["basics"]["optimizable"])

    def test_session_conversation_uses_authenticated_package_memory(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        from backend import memory
        from backend.routers.resume import AutofillReq, _resolve_conversation

        sid = "12345678-1234-4234-8234-123456789abc"
        anon = self.H["X-Anon-Id"]
        req = AutofillReq(session_id=sid, template_version=self.ver)
        messages = [{"id": 1, "role": "user", "content": "实现课程推荐"}]
        bundle = {"session": {"id": sid}, "messages": messages,
                  "profile": {"profile": {"summary": "课程推荐"}}}
        with patch.object(memory, "verify_session", return_value=True) as verify, patch.object(
                memory, "get_session_bundle", return_value=bundle) as read:
            self.assertEqual(_resolve_conversation(req, anon, "test-token"),
                             (messages, bundle["profile"]["profile"]))
            verify.assert_called_once_with(sid, anon, "test-token")
            read.assert_called_once_with(sid)
        with patch.object(memory, "verify_session", return_value=False), patch.object(
                memory, "get_session_bundle") as read:
            with self.assertRaises(HTTPException) as caught:
                _resolve_conversation(req, anon, "forged")
            self.assertEqual(caught.exception.status_code, 404)
            read.assert_not_called()
        for missing in (None, {"session": None, "messages": []}):
            with self.subTest(missing=missing), patch.object(
                    memory, "verify_session", return_value=True), patch.object(
                    memory, "get_session_bundle", return_value=missing):
                with self.assertRaises(HTTPException) as caught:
                    _resolve_conversation(req, anon, "test-token")
                self.assertEqual(caught.exception.status_code, 404)
        with patch.object(memory, "verify_session", return_value=True), patch.object(
                memory, "get_session_bundle", side_effect=memory.MemoryStoreError("private upstream detail")):
            with self.assertRaises(HTTPException) as caught:
                _resolve_conversation(req, anon, "test-token")
            self.assertEqual(caught.exception.status_code, 503)
            self.assertNotIn("private upstream detail", str(caught.exception.detail))

    def test_nonempty_sections_reach_model_and_complete(self):
        from unittest.mock import patch
        from backend.routers import resume as routes
        from backend import resume_optimizer as optimizer

        original = "实现课程推荐"
        for section in ("projects", "internships", "summary"):
            with self.subTest(section=section):
                data = {"text": original} if section == "summary" else [{"bullets": [original]}]
                output = {"rewritten": [{"original": original, "suggested": original}], "questions": []}
                with patch.object(routes.security, "enforce_ip"), patch.object(
                        routes.security, "charge_turn"), patch.object(
                        optimizer.llm, "chat_json", return_value=output) as model:
                    response = self.c.post("/api/v2/resume/optimize/section", headers=self.H, json={
                        "template_version": self.ver, "section": section,
                        "fields": {section: data}})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "done")
                model.assert_called_once()
                self.assertEqual(response.json()["optimized"], data)

    def test_unsupported_claim_with_separate_placeholder_is_not_written(self):
        from unittest.mock import patch
        from backend.routers import resume as routes
        from backend import resume_optimizer as optimizer

        original = "实现课程推荐"
        for suggested in ("实现课程推荐，提升85%【待补充：环境】",
                          "使用 K8s 实现课程推荐【待补充：环境】"):
            with self.subTest(suggested=suggested):
                output = {"rewritten": [{"original": original, "suggested": suggested}], "questions": []}
                with patch.object(routes.security, "enforce_ip"), patch.object(
                        routes.security, "charge_turn"), patch.object(
                        optimizer.llm, "chat_json", return_value=output):
                    response = self.c.post("/api/v2/resume/optimize/section", headers=self.H, json={
                        "template_version": self.ver, "section": "projects",
                        "fields": {"projects": [{"bullets": [original]}]}})
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(result["status"], "done")
                safe = original + "【待补充】"
                self.assertEqual(result["optimized"][0]["bullets"], [safe])
                self.assertEqual(result["change_log"][0]["after"], safe)
                self.assertTrue(result["needs_input"])

    def test_section_rejects_non_optimizable(self):
        fields = json.loads((FIXTURE / "autofill.json").read_text(encoding="utf-8"))["fields"]
        r = self.c.post("/api/v2/resume/optimize/section", headers=self.H,
                        json={"template_version": self.ver, "section": "basics", "fields": fields})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()

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
        from backend.routers.resume import router
        app = FastAPI()
        app.include_router(router)
        cls.c = TestClient(app)
        cls.ver = cls.c.get("/api/v2/resume/template").json()["template_version"]

    def test_template_endpoint(self):
        r = self.c.get("/api/v2/resume/template")
        self.assertEqual(r.status_code, 200)
        self.assertIn("sections", r.json())

    def test_version_mismatch_409(self):
        r = self.c.post("/api/v2/resume/autofill",
                        json={"template_version": "0.0.0", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 409)
        r = self.c.post("/api/v2/resume/optimize/plan",
                        json={"template_version": "0.0.0", "fields": {}})
        self.assertEqual(r.status_code, 409)

    def test_autofill_requires_conversation(self):
        r = self.c.post("/api/v2/resume/autofill",
                        json={"template_version": self.ver})
        self.assertEqual(r.status_code, 400)

    def test_autofill_empty_conversation_gaps(self):
        r = self.c.post("/api/v2/resume/autofill",
                        json={"template_version": self.ver,
                              "messages": [{"role": "assistant", "content": "你好"}]})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["gaps"])

    def test_plan_shape_and_order(self):
        fields = json.loads((FIXTURE / "autofill.json").read_text(encoding="utf-8"))["fields"]
        r = self.c.post("/api/v2/resume/optimize/plan",
                        json={"template_version": self.ver, "fields": fields})
        self.assertEqual(r.status_code, 200)
        secs = r.json()["sections"]
        prios = [s["priority"] for s in secs]
        self.assertEqual(prios, sorted(prios))
        by_key = {s["key"]: s for s in secs}
        self.assertEqual(by_key["projects"]["status"], "pending")
        self.assertEqual(by_key["internships"]["status"], "skipped")
        self.assertFalse(by_key["basics"]["optimizable"])

    def test_section_rejects_non_optimizable(self):
        fields = json.loads((FIXTURE / "autofill.json").read_text(encoding="utf-8"))["fields"]
        r = self.c.post("/api/v2/resume/optimize/section",
                        json={"template_version": self.ver, "section": "basics", "fields": fields})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()

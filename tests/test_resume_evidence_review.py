"""Offline autofill evidence regressions: real msg_id, concrete leaf, verbatim value.

No network, no real model: only the route + a mocked chat_json payload.
"""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.resume_template import load
from backend.routers import resume as routes

ANON = "11111111-1111-4111-8111-111111111111"
USER_TEXT = "我做过校园二手交易小程序，也做过课程推荐系统，是后端负责人"
PROJECT = "校园二手交易小程序"
SECOND = "课程推荐系统"


class AutofillEvidenceReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(routes.router)
        cls.c = TestClient(app)
        cls.ver = load().template_version

    def autofill(self, messages, data):
        with patch.object(routes.security, "enforce_ip"), \
                patch.object(routes.security, "charge_turn"), \
                patch.object(routes.llm, "chat_json", return_value=data):
            response = self.c.post("/api/v2/resume/autofill",
                                   headers={"X-Anon-Id": ANON},
                                   json={"template_version": self.ver,
                                         "messages": messages})
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def messages():
        # Absent ids fall back to the displayed index, which the prompt shows.
        return [{"role": "user", "content": USER_TEXT}]

    def fields(self, names):
        return {"projects": [{"name": name} for name in names]}

    def test_verbatim_value_with_real_msg_id_is_kept(self):
        body = self.autofill(self.messages(), {
            "fields": self.fields([PROJECT]),
            "evidence_map": {"projects.0.name": [
                {"msg_id": 0, "quote": "我做过校园二手交易小程序"}]},
        })
        self.assertEqual(body["fields"]["projects"][0]["name"], PROJECT)
        self.assertIn("projects.0.name", body["evidence_map"])
        self.assertNotIn("projects.0.name", {g["field_path"] for g in body["gaps"]})

    def test_forged_or_unknown_msg_id_cannot_back_a_value(self):
        for msg_id in (7, "m42", "", None, True, 0.0):
            with self.subTest(msg_id=msg_id):
                body = self.autofill(self.messages(), {
                    "fields": self.fields([PROJECT]),
                    "evidence_map": {"projects.0.name": [
                        {"msg_id": msg_id, "quote": "我做过校园二手交易小程序"}]},
                })
                self.assertEqual(body["fields"]["projects"][0]["name"], "")
                self.assertNotIn("projects.0.name", body["evidence_map"])
                self.assertIn("projects.0.name", {g["field_path"] for g in body["gaps"]})

    def test_unrelated_but_real_quote_cannot_back_a_value(self):
        # 真实原话（含 msg_id），但字段值不在该 quote 里 → 不允许背书
        body = self.autofill(self.messages(), {
            "fields": self.fields([PROJECT]),
            "evidence_map": {"projects.0.name": [
                {"msg_id": 0, "quote": "是后端负责人"}]},
        })
        self.assertEqual(body["fields"]["projects"][0]["name"], "")

    def test_ancestor_or_section_evidence_cannot_back_a_leaf(self):
        for path in ("projects", "projects.0"):
            with self.subTest(path=path):
                body = self.autofill(self.messages(), {
                    "fields": self.fields([PROJECT]),
                    "evidence_map": {path: [
                        {"msg_id": 0, "quote": "我做过校园二手交易小程序"}]},
                })
                self.assertEqual(body["fields"]["projects"][0]["name"], "")
                self.assertNotIn("projects.0.name", body["evidence_map"])

    def test_assistant_message_cannot_be_evidence(self):
        messages = [{"id": 0, "role": "assistant", "content": USER_TEXT},
                    {"id": 1, "role": "user", "content": "你好"}]
        body = self.autofill(messages, {
            "fields": self.fields([PROJECT]),
            "evidence_map": {"projects.0.name": [
                {"msg_id": 0, "quote": "我做过校园二手交易小程序"}]},
        })
        self.assertEqual(body["fields"]["projects"][0]["name"], "")

    def test_duplicate_ids_are_ambiguous_and_rejected(self):
        messages = [{"id": 1, "role": "user", "content": USER_TEXT},
                    {"id": 1, "role": "user", "content": USER_TEXT}]
        body = self.autofill(messages, {
            "fields": self.fields([PROJECT]),
            "evidence_map": {"projects.0.name": [
                {"msg_id": 1, "quote": "我做过校园二手交易小程序"}]},
        })
        self.assertEqual(body["fields"]["projects"][0]["name"], "")

    def test_malformed_model_payload_is_sanitized_not_500(self):
        body = self.autofill(self.messages(), {
            "fields": {"projects": "not-a-list",
                       "basics": [1, 2, 3],
                       "skills": {"groups": "nope"},
                       "education": [{"school": ["x"], "courses": "Java"}]},
            "evidence_map": {"projects.0.name": "nope",
                             "x": [None, "y", {"msg_id": {}, "quote": 1}],
                             "projects.1.name": [{"msg_id": 0, "quote": USER_TEXT}]},
            "gaps": "nope",
        })
        self.assertEqual(body["fields"]["projects"], [])
        self.assertEqual(body["fields"]["basics"]["name"], "")
        self.assertEqual(body["evidence_map"], {})

    def test_source_indexes_are_preserved_so_evidence_cannot_retarget(self):
        body = self.autofill(self.messages(), {
            "fields": {"projects": [{}, {"name": SECOND}]},
            "evidence_map": {"projects.1.name": [
                {"msg_id": 0, "quote": "也做过课程推荐系统"}]},
        })
        projects = body["fields"]["projects"]
        self.assertEqual(len(projects), 2)           # 空行不压缩，索引不漂移
        self.assertEqual(projects[0]["name"], "")
        self.assertEqual(projects[1]["name"], SECOND)
        self.assertIn("projects.1.name", body["evidence_map"])


if __name__ == "__main__":
    unittest.main()

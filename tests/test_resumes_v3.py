"""Offline /api/v3/resumes* tests: CRUD, CAS If-Match, stale_sources, regenerate."""
import importlib
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://supabase.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("DASHSCOPE_API_KEY", "test-key")

from fastapi import FastAPI                    # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402

rv3 = importlib.import_module("backend.routers.resumes_v3")
mv3 = importlib.import_module("backend.memory_v3")
rb = importlib.import_module("backend.resume_builder_v3")

OID = "12345678-1234-4234-8234-123456789abc"
DID = "d0c0ffee-2222-4000-8000-000000000001"
OWNER = {"id": OID, "memory_enabled": True}
DOC = {"id": DID, "owner_id": OID, "title": "后端简历", "target_role": "后端",
       "template_version": "tpl-x", "status": "draft", "version": 3,
       "fields": {"basics": {"name": "张明", "phone": "", "email": "",
                             "city": "杭州", "site": ""},
                  "_meta": {"manual_override": ["basics.city"]}},
       "evidence_map": {}, "gaps": [],
       "fact_snapshot": [{"fact_id": "f1", "value_hash": "h1"}],
       "created_at": "t0", "updated_at": "t1", "deleted_at": None}
H = {"X-Owner-Token": "t" * 40}


class ResumesV3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(rv3.router)
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        for p in [patch.object(rv3.security, "enforce_ip", lambda r: None),
                  patch.object(rv3.security_v3, "require_owner",
                               lambda t: OWNER),
                  # 默认额度充足，避免 RPC 触网；限额用例各自覆盖
                  patch.object(rv3.memory_v3, "spend_daily_quota",
                               lambda oid, kind, cap: 1)]:
            p.start()
            self.addCleanup(p.stop)

    def _patch(self, name, fn):
        p = patch.object(rv3.memory_v3, name, fn)
        p.start()
        self.addCleanup(p.stop)

    def test_list_projects_without_fields(self):
        self._patch("list_documents", lambda oid: [DOC])
        r = self.client.get("/api/v3/resumes", headers=H)
        self.assertEqual(r.status_code, 200)
        d = r.json()["documents"][0]
        self.assertEqual(set(d), {"id", "title", "target_role", "status",
                                  "completeness", "updated_at"})
        self.assertNotIn("fields", d)
        self.assertGreater(d["completeness"], 0)

    def test_get_detail_adds_stale_sources(self):
        self._patch("get_document", lambda oid, did: DOC)
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("stale_sources", lambda doc, facts:
                    [{"field_path": "p", "fact_id": "f1", "change": "deleted"}])
        r = self.client.get(f"/api/v3/resumes/{DID}", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("owner_id", r.json())
        self.assertEqual(r.json()["stale_sources"][0]["change"], "deleted")
        self._patch("get_document", lambda oid, did: None)
        self.assertEqual(self.client.get(f"/api/v3/resumes/{DID}",
                                         headers=H).status_code, 404)

    def test_create_builds_and_persists(self):
        doc = dict(DOC)
        seen = {}
        def fake_create(oid, **kw):
            seen.update(kw)
            return doc
        with patch.object(rv3.resume_builder_v3, "create_document",
                          fake_create):
            self._patch("facts_for_generation", lambda oid: [])
            self._patch("stale_sources", lambda d, f: [])
            r = self.client.post("/api/v3/resumes", headers=H, json={
                "title": "后端简历", "target_role": "后端",
                "jd_text": "需要 FastAPI", "template_version": "tpl-x"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["document"]["id"], DID)
        self.assertEqual(seen["jd_text"], "需要 FastAPI")

    def test_create_build_failed_503(self):
        with patch.object(rv3.resume_builder_v3, "create_document",
                          side_effect=rb.BuildFailed("x")):
            r = self.client.post("/api/v3/resumes", headers=H,
                                 json={"title": "t"})
        self.assertEqual(r.status_code, 503)

    def test_patch_requires_if_match_and_cas(self):
        self._patch("get_document", lambda oid, did: DOC)
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("stale_sources", lambda d, f: [])
        r = self.client.patch(f"/api/v3/resumes/{DID}", headers=H,
                              json={"title": "新标题"})
        self.assertEqual(r.status_code, 422)
        seen = {}
        self._patch("patch_document",
                    lambda oid, did, u, ev, **kw: seen.update(
                        {"updates": u, "ev": ev}) or {**DOC, **u, "version": 4})
        h = {**H, "If-Match": "3"}
        r = self.client.patch(f"/api/v3/resumes/{DID}", headers=h,
                              json={"title": "新标题"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(seen["ev"], 3)
        self.assertEqual(seen["updates"], {"title": "新标题"})

    def test_patch_fields_tracks_manual_override(self):
        self._patch("get_document", lambda oid, did: DOC)
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("stale_sources", lambda d, f: [])
        seen = {}
        self._patch("patch_document",
                    lambda oid, did, u, ev, **kw: seen.update(u) or DOC)
        new_fields = {"basics": {"name": "张明", "phone": "138",
                                 "email": "", "city": "上海", "site": ""}}
        h = {**H, "If-Match": "3"}
        r = self.client.patch(f"/api/v3/resumes/{DID}", headers=h,
                              json={"fields": new_fields})
        self.assertEqual(r.status_code, 200)
        mo = set(seen["fields"]["_meta"]["manual_override"])
        self.assertIn("basics.city", mo)     # prior manual kept
        self.assertIn("basics.phone", mo)    # newly hand-edited leaf

    def test_patch_conflict_409(self):
        self._patch("get_document", lambda oid, did: DOC)
        self._patch("patch_document",
                    lambda *a, **k: (_ for _ in ()).throw(
                        mv3.V3Conflict("race")))
        h = {**H, "If-Match": "3"}
        r = self.client.patch(f"/api/v3/resumes/{DID}", headers=h,
                              json={"title": "x"})
        self.assertEqual(r.status_code, 409)

    def test_protocol3_on_resume_endpoints(self):
        self._patch("list_documents", lambda oid: [DOC])
        self._patch("get_document", lambda oid, did: DOC)
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("stale_sources", lambda d, f: [])
        self._patch("soft_delete_document", lambda oid, did: True)
        for r in [self.client.get("/api/v3/resumes", headers=H),
                  self.client.get(f"/api/v3/resumes/{DID}", headers=H),
                  self.client.delete(f"/api/v3/resumes/{DID}", headers=H)]:
            self.assertEqual(r.json().get("protocol"), 3, r.url)

    def test_stale_flow_fact_deleted_then_banner(self):
        """#42 链路：evidence 引用的事实被删 → 详情 stale_sources 标 deleted。"""
        doc = {**DOC,
               "evidence_map": {"projects.0.name": [
                   {"fact_id": "f1", "quote": "做过小程序"}]},
               "fact_snapshot": [{"fact_id": "f1", "value_hash": "h1"}]}
        self._patch("get_document", lambda oid, did: doc)
        # 事实已删：facts_for_generation 不再包含 f1
        self._patch("facts_for_generation", lambda oid: [])
        r = self.client.get(f"/api/v3/resumes/{DID}", headers=H)
        stale = r.json()["stale_sources"]
        self.assertEqual(stale, [{"field_path": "projects.0.name",
                                  "fact_id": "f1", "change": "deleted"}])
        # 事实值变了（hash 不同）→ updated
        live = [{"id": "f1", "value_hash": "h2", "status": "confirmed",
                 "deleted_at": None}]
        self._patch("facts_for_generation", lambda oid: live)
        stale = self.client.get(f"/api/v3/resumes/{DID}", headers=H) \
            .json()["stale_sources"]
        self.assertEqual(stale[0]["change"], "updated")

    def test_delete_soft(self):
        self._patch("soft_delete_document", lambda oid, did: True)
        r = self.client.delete(f"/api/v3/resumes/{DID}", headers=H)
        self.assertEqual(r.json()["deleted"], DID)
        self._patch("soft_delete_document", lambda oid, did: False)
        self.assertEqual(self.client.delete(f"/api/v3/resumes/{DID}",
                                            headers=H).status_code, 404)

    def test_duplicate_creates_fresh_draft(self):
        """#69：复制 → 新 id、version=0、status=draft、title 带（副本）。"""
        self._patch("get_document", lambda oid, did: dict(DOC))
        self._patch("stale_sources", lambda d, f: [])
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("duplicate_document", lambda oid, d: {
            **d, "id": "dup-1", "title": d["title"] + "（副本）",
            "version": 0, "status": "draft"})
        r = self.client.post(f"/api/v3/resumes/{DID}/duplicate", headers=H)
        self.assertEqual(r.status_code, 200)
        doc = r.json()["document"]
        self.assertEqual(doc["id"], "dup-1")
        self.assertEqual(doc["status"], "draft")

    def test_versions_list_and_restore(self):
        """#68：列历史 + restore 用快照内容做一次带快照的 CAS 写。"""
        self._patch("get_document", lambda oid, did: dict(DOC))
        self._patch("stale_sources", lambda d, f: [])
        self._patch("list_document_versions", lambda oid, did: [
            {"id": "v2", "version": 2, "created_at": "t"}])
        r = self.client.get(f"/api/v3/resumes/{DID}/versions", headers=H)
        self.assertEqual(r.json()["versions"][0]["version"], 2)
        self._patch("get_document_version", lambda oid, did, v: {
            "snapshot": {"title": "旧标题", "fields": {"x": 1}}})
        captured = {}
        def fake_patch(oid, did, updates, expected, **kw):
            captured.update(updates)
            captured["snapshot_given"] = kw.get("snapshot") is not None
            return {**DOC, **updates, "version": expected + 1}
        self._patch("patch_document", fake_patch)
        self._patch("facts_for_generation", lambda oid: [])
        r = self.client.post(f"/api/v3/resumes/{DID}/versions/2/restore",
                             headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured["title"], "旧标题")
        self.assertTrue(captured["snapshot_given"])
        self._patch("get_document_version", lambda oid, did, v: None)
        self.assertEqual(
            self.client.post(f"/api/v3/resumes/{DID}/versions/9/restore",
                             headers=H).status_code, 404)

    def test_create_daily_quota_429(self):
        """#67：日生成额度尽 → 429，不调生成器。"""
        self._patch("spend_daily_quota", lambda oid, kind, cap: -1)
        r = self.client.post("/api/v3/resumes", headers=H,
                             json={"title": "x", "template_version": "tpl-x"})
        self.assertEqual(r.status_code, 429)

    def test_batch_daily_quota_marks_item(self):
        """#67：批量中额度尽的项标 daily_quota，不拖垮整批。"""
        spends = iter([1, -1])
        self._patch("spend_daily_quota",
                    lambda oid, kind, cap: next(spends, -1))
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("stale_sources", lambda d, f: [])
        with patch.object(rv3.resume_builder_v3, "create_document",
                          lambda oid, **kw: {**DOC, "title": kw["title"]}):
            r = self.client.post("/api/v3/resumes/batch", headers=H, json={
                "items": [{"title": "甲"}, {"title": "乙"}]})
        res = r.json()["results"]
        self.assertEqual(res[0]["document"]["title"], "甲")
        self.assertEqual(res[1]["error"], "daily_quota")

    def test_batch_isolated_and_coverage(self):
        docs = []
        def fake_create(oid, **kw):
            if kw["title"] == "失败项":
                raise rb.BuildFailed("x")
            d = {**DOC, "id": f"doc-{len(docs)}", "title": kw["title"],
                 "fields": {"skills": {"groups": [{"name": "后端",
                                                 "items": ["Python"]}]}}}
            docs.append(d)
            return d
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("stale_sources", lambda d, f: [])
        with patch.object(rv3.resume_builder_v3, "create_document",
                          fake_create):
            r = self.client.post("/api/v3/resumes/batch", headers=H, json={
                "items": [
                    {"title": "后端稿", "jd_text": "需要 Python 和 PostgreSQL"},
                    {"title": "失败项"},
                ]})
        self.assertEqual(r.status_code, 200)
        res = r.json()["results"]
        self.assertEqual(res[0]["document"]["title"], "后端稿")
        cov = res[0]["jd_coverage"]
        self.assertIn("python", cov["covered"])
        self.assertIn("postgresql", cov["missing"])
        self.assertEqual(res[1]["error"], "build_failed")
        # 空批/超上限 → 422
        self.assertEqual(self.client.post(
            "/api/v3/resumes/batch", headers=H, json={"items": []}).status_code,
            422)

    def test_regenerate_draft_and_cas(self):
        self._patch("get_document", lambda oid, did: DOC)
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("stale_sources", lambda d, f: [])
        built = {"fields": {"basics": {"name": "重建"}}, "evidence_map": {},
                 "gaps": [], "fact_snapshot": [{"fact_id": "f2",
                                              "value_hash": "h2"}]}
        seen = {}
        self._patch("patch_document",
                    lambda oid, did, u, ev, **kw: seen.update(
                        {"u": u, "ev": ev}) or {**DOC, **u})
        regen_kw = {}
        def fake_regen(doc, tpl, **kw):
            regen_kw.update(kw)
            return built
        with patch.object(rv3.resume_builder_v3, "regenerate_fields",
                          fake_regen):
            r = self.client.post(f"/api/v3/resumes/{DID}/regenerate",
                                 headers=H, json={"keep_manual": True,
                                                  "jd_text": "JD"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(seen["ev"], DOC["version"])
        self.assertEqual(seen["u"]["status"], "draft")
        self.assertEqual(seen["u"]["fact_snapshot"], built["fact_snapshot"])
        self.assertTrue(seen["u"]["jd_digest"])
        self.assertTrue(regen_kw["keep_manual"])


if __name__ == "__main__":
    unittest.main()

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
                               lambda t: OWNER)]:
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
                    lambda oid, did, u, ev: seen.update(
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
                    lambda oid, did, u, ev: seen.update(u) or DOC)
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

    def test_regenerate_draft_and_cas(self):
        self._patch("get_document", lambda oid, did: DOC)
        self._patch("facts_for_generation", lambda oid: [])
        self._patch("stale_sources", lambda d, f: [])
        built = {"fields": {"basics": {"name": "重建"}}, "evidence_map": {},
                 "gaps": [], "fact_snapshot": [{"fact_id": "f2",
                                              "value_hash": "h2"}]}
        seen = {}
        self._patch("patch_document",
                    lambda oid, did, u, ev: seen.update(
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

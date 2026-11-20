"""Offline /api/v3/* router contract tests: auth, limits, error mapping."""
import importlib
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://supabase.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("DASHSCOPE_API_KEY", "test-key")

from fastapi import FastAPI                    # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402

rv3 = importlib.import_module("backend.routers.memory_v3")
mv3 = importlib.import_module("backend.memory_v3")
fx = importlib.import_module("backend.fact_extractor")
ss3 = importlib.import_module("backend.session_security_v3")

OID = "12345678-1234-4234-8234-123456789abc"
OWNER = {"id": OID, "memory_enabled": True,
         "consent_at": "2026-11-20T00:00:00+00:00"}
FACT = {"id": "aaaaaaaa-0000-4000-8000-000000000001", "owner_id": OID,
        "kind": "skill", "slot_path": "skills.groups.0.items.0",
        "value": "Python", "status": "confirmed", "confidence": 0.9,
        "value_hash": "h" * 64, "source_session_id": None,
        "source_message_id": 5,
        "evidence": [{"session_id": "s", "msg_id": 5, "quote": "会 Python"}],
        "created_at": "2026-11-20T00:00:00+00:00", "deleted_at": None}
H = {"X-Owner-Token": "t" * 40}


class RouterV3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(rv3.router)
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        ps = [patch.object(rv3.security, "enforce_ip", lambda r: None),
              patch.object(rv3.security_v3, "require_owner", lambda t: OWNER)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    def _patch(self, name, fn):
        p = patch.object(rv3.memory_v3, name, fn)
        p.start()
        self.addCleanup(p.stop)

    # --- auth -----------------------------------------------------------------
    def test_no_or_bad_token_uniform_404(self):
        with patch.object(rv3.security_v3, "require_owner",
                          side_effect=rv3.security.not_found()):
            for call in [self.client.get("/api/v3/memory"),
                         self.client.get("/api/v3/facts", headers=H),
                         self.client.delete("/api/v3/facts/x", headers=H)]:
                self.assertEqual(call.status_code, 404)

    # --- memory lifecycle -----------------------------------------------------
    def test_enable_mints_once(self):
        bundle = {"owner": OWNER, "owner_token": "tok", "recovery_code": "REC"}
        self._patch("get_owner_by_key_hash", lambda h: None)
        with patch.object(rv3.security_v3, "issue_owner", lambda a: bundle):
            r = self.client.post("/api/v3/memory/enable", json={"recovery": True})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["owner_token"], "tok")
        self.assertEqual(body["recovery_code"], "REC")
        self.assertEqual(body["owner_id"], OID)

    def test_enable_reactivates_disabled_owner(self):
        disabled = {**OWNER, "memory_enabled": False}
        self._patch("get_owner_by_key_hash", lambda h: disabled)
        calls = []
        self._patch("set_memory_enabled",
                    lambda oid, en: calls.append((oid, en)) or OWNER)
        r = self.client.post("/api/v3/memory/enable",
                             headers=H, json={"recovery": False})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["reactivated"])
        self.assertEqual(calls, [(OID, True)])

    def test_status_shape(self):
        self._patch("fact_counts", lambda oid: {
            "total": 3, "by_kind": {"skill": 2}, "by_status": {"confirmed": 1}})
        self._patch("last_extracted_at", lambda oid: "2026-11-20T01:00:00+00:00")
        r = self.client.get("/api/v3/memory", headers=H)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["fact_count"], 3)
        self.assertEqual(body["by_kind"], {"skill": 2})
        self.assertEqual(body["consent_at"], OWNER["consent_at"])

    def test_disable(self):
        self._patch("set_memory_enabled",
                    lambda oid, en: {**OWNER, "memory_enabled": en})
        r = self.client.post("/api/v3/memory/disable", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])

    def test_delete_all_requires_phrase(self):
        r = self.client.request("DELETE", "/api/v3/memory", headers=H,
                                json={"confirm": "yes"})
        self.assertEqual(r.status_code, 422)
        self._patch("delete_owner", lambda oid: {"v3_facts": 4, "v3_owner": 1})
        r = self.client.request("DELETE", "/api/v3/memory", headers=H,
                                json={"confirm": "DELETE_ALL"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["deleted"]["v3_facts"], 4)

    def test_export_attachment(self):
        self._patch("export_owner", lambda oid: {
            "schema_version": 1, "owner": {"id": OID}, "facts": [FACT],
            "documents": []})
        r = self.client.get("/api/v3/memory/export", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        body = r.json()
        self.assertEqual(body["protocol"], 3)
        self.assertEqual(body["facts"][0]["id"], FACT["id"])

    def test_retention_warning_flag(self):
        self._patch("fact_counts", lambda oid:
                    {"total": 0, "by_kind": {}, "by_status": {}})
        self._patch("last_extracted_at", lambda oid: None)
        r = self.client.get("/api/v3/memory", headers=H)
        self.assertFalse(r.json()["retention_warning"])
        old = {**OWNER, "last_seen_at": "2024-01-01T00:00:00+00:00"}
        with patch.object(rv3.security_v3, "require_owner", lambda t: old):
            r = self.client.get("/api/v3/memory", headers=H)
        self.assertTrue(r.json()["retention_warning"])
        self.assertEqual(r.json()["retention_days"], 365)

    # --- facts ----------------------------------------------------------------
    def test_facts_list_projection_no_leak(self):
        self._patch("list_facts", lambda oid, **kw:
                    {"facts": [FACT], "next_cursor": None})
        r = self.client.get("/api/v3/facts", headers=H)
        self.assertEqual(r.status_code, 200)
        f = r.json()["facts"][0]
        self.assertEqual(set(f), {"id", "kind", "slot_path", "value",
                                  "status", "evidence", "created_at"})
        self.assertNotIn("value_hash", f)
        self.assertNotIn("owner_id", f)

    def test_facts_filters_and_limit(self):
        seen = {}
        self._patch("list_facts", lambda oid, **kw:
                    seen.update(kw) or {"facts": [], "next_cursor": "c~d"})
        r = self.client.get("/api/v3/facts?kind=skill&status=confirmed"
                            "&cursor=a~b&limit=10", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(seen["kind"], "skill")
        self.assertEqual(r.json()["next_cursor"], "c~d")
        self.assertEqual(
            self.client.get("/api/v3/facts?limit=0", headers=H).status_code, 422)
        self.assertEqual(
            self.client.get("/api/v3/facts?limit=9999", headers=H).status_code,
            422)
        self._patch("list_facts", lambda oid, **kw: (_ for _ in ()).throw(
            ValueError("bad")))
        self.assertEqual(
            self.client.get("/api/v3/facts?kind=bogus", headers=H).status_code,
            422)

    def test_patch_fact_status_and_value(self):
        self._patch("get_fact", lambda oid, fid: FACT)
        self._patch("set_fact_status",
                    lambda oid, fid, s: {**FACT, "status": s})
        r = self.client.patch(f"/api/v3/facts/{FACT['id']}", headers=H,
                              json={"status": "rejected"})
        self.assertEqual(r.json()["fact"]["status"], "rejected")
        new = {**FACT, "id": "new-id", "value": "Rust"}
        self._patch("supersede_fact", lambda oid, fid, v, **kw: new)
        r = self.client.patch(f"/api/v3/facts/{FACT['id']}", headers=H,
                              json={"value": "Rust"})
        self.assertEqual(r.json()["fact"]["id"], "new-id")
        self.assertEqual(
            self.client.patch(f"/api/v3/facts/{FACT['id']}", headers=H,
                              json={}).status_code, 422)
        self.assertEqual(
            self.client.patch(f"/api/v3/facts/{FACT['id']}", headers=H,
                              json={"status": "superseded"}).status_code, 422)

    def test_patch_missing_fact_404(self):
        self._patch("get_fact", lambda oid, fid: None)
        r = self.client.patch(f"/api/v3/facts/{FACT['id']}", headers=H,
                              json={"status": "rejected"})
        self.assertEqual(r.status_code, 404)

    def test_delete_fact(self):
        self._patch("soft_delete_fact", lambda oid, fid: True)
        r = self.client.delete(f"/api/v3/facts/{FACT['id']}", headers=H)
        self.assertEqual(r.json()["deleted"], FACT["id"])
        self._patch("soft_delete_fact", lambda oid, fid: False)
        self.assertEqual(
            self.client.delete(f"/api/v3/facts/{FACT['id']}", headers=H)
            .status_code, 404)

    # --- extract ---------------------------------------------------------------
    def test_extract_passthrough_and_errors(self):
        result = {"added": 1, "merged": 0, "conflicts": [], "skipped": 2}
        with patch.object(rv3.fact_extractor, "extract_incremental",
                          lambda oid, sid: result):
            r = self.client.post("/api/v3/facts/extract", headers=H,
                                 json={"session_id":
                                       "87654321-4321-4321-8321-cba987654321"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["added"], 1)
        with patch.object(rv3.fact_extractor, "extract_incremental",
                          side_effect=mv3.V3NotFound("x")):
            self.assertEqual(self.client.post(
                "/api/v3/facts/extract", headers=H,
                json={"session_id":
                      "87654321-4321-4321-8321-cba987654321"}).status_code, 404)
        with patch.object(rv3.fact_extractor, "extract_incremental",
                          side_effect=fx.ExtractionFailed("x")):
            self.assertEqual(self.client.post(
                "/api/v3/facts/extract", headers=H,
                json={"session_id":
                      "87654321-4321-4321-8321-cba987654321"}).status_code, 503)

    def test_backfill_explicit_and_auto(self):
        rv3._backfill_hits.clear()
        calls = []
        def fake_extract(oid, sid):
            calls.append(sid)
            if sid.endswith("0002"):
                raise mv3.V3NotFound("x")
            return {"added": 1}
        with patch.object(rv3.fact_extractor, "extract_incremental",
                          fake_extract):
            body = {"session_ids": [
                "87654321-4321-4321-8321-cba987650001",
                "87654321-4321-4321-8321-cba987650002",
                "87654321-4321-4321-8321-cba987650003"]}
            r = self.client.post("/api/v3/memory/backfill", headers=H,
                                 json=body)
        self.assertEqual(r.status_code, 200)
        res = r.json()
        self.assertEqual(res["processed"], 3)
        self.assertEqual(res["results"][1]["error"], "not_found")
        # 无效 session_id → 422
        self.assertEqual(self.client.post(
            "/api/v3/memory/backfill", headers=H,
            json={"session_ids": ["bogus"]}).status_code, 422)

    def test_extract_per_owner_cap(self):
        rv3._extract_hits.clear()
        with patch.object(rv3.fact_extractor, "extract_incremental",
                          return_value={"added": 0}):
            for _ in range(rv3.EXTRACT_PER_HOUR):
                r = self.client.post(
                    "/api/v3/facts/extract", headers=H,
                    json={"session_id":
                          "87654321-4321-4321-8321-cba987654321"})
                self.assertEqual(r.status_code, 200)
            r = self.client.post(
                "/api/v3/facts/extract", headers=H,
                json={"session_id":
                      "87654321-4321-4321-8321-cba987654321"})
            self.assertEqual(r.status_code, 429)


if __name__ == "__main__":
    unittest.main()

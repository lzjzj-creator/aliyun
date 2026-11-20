"""Offline v3 storage contract tests. All I/O is httpx.MockTransport; no services."""
import copy
import hashlib
import importlib
import json
import os
import re
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

# Load with dummy configuration, never the developer's service credentials.
with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
}):
    memory = importlib.import_module("backend.memory")
    memory_v3 = importlib.import_module("backend.memory_v3")

OID = "12345678-1234-4234-8234-123456789abc"
SID = "87654321-4321-4321-8321-cba987654321"
TABLES = ("v3_owner", "v3_facts", "v3_extraction_state", "v3_resume_documents")


class Store:
    """Small REST model for v3 requests/CAS; not a PostgreSQL emulator."""
    def __init__(self):
        self.rows = {t: [] for t in TABLES}
        self.requests = []
        self.lock = threading.Lock()

    @staticmethod
    def filtered(rows, params):
        for field, value in params.items():
            if field in ("select", "order", "limit", "offset", "or"):
                continue
            if value.startswith("eq."):
                rows = [r for r in rows if str(r.get(field)) == value[3:]]
            elif value == "is.null":
                rows = [r for r in rows if r.get(field) is None]
            elif value == "not.is.null":
                rows = [r for r in rows if r.get(field) is not None]
            elif value.startswith("in.("):
                allowed = set(value[4:-1].split(","))
                rows = [r for r in rows if r.get(field) in allowed]
        or_spec = params.get("or")
        if or_spec:
            m = re.fullmatch(
                r"\(created_at\.gt\.(.+),and\(created_at\.eq\.(.+),id\.gt\.(.+)\)\)",
                or_spec)
            assert m, f"unmodeled or filter: {or_spec}"
            gt_ts, eq_ts, gt_id = m.groups()
            rows = [r for r in rows
                    if r.get("created_at", "") > gt_ts
                    or (r.get("created_at", "") == eq_ts and r.get("id", "") > gt_id)]
        return rows

    @staticmethod
    def sorted(rows, order):
        for key in reversed((order or "").split(",")):
            key = key.strip()
            if not key:
                continue
            field, _, direction = key.partition(".")
            rows = sorted(rows, key=lambda r: (r.get(field) is None, r.get(field, "")),
                          reverse=(direction == "desc"))
        return rows

    @staticmethod
    def projected(rows, select):
        if not select or select == "*":
            return rows
        fields = set(select.split(","))
        return [{k: v for k, v in r.items() if k in fields} for r in rows]

    def __call__(self, request):
        with self.lock:
            return self.handle(request)

    def handle(self, request):
        self.requests.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        data = json.loads(request.content) if request.content else {}
        rows = self.filtered(list(self.rows.get(table, [])), params)
        if request.method == "GET":
            rows = self.sorted(rows, params.get("order"))
            offset = int(params.get("offset", 0))
            limit = int(params.get("limit", 1000))
            return httpx.Response(200, json=copy.deepcopy(
                self.projected(rows[offset:offset + limit], params.get("select"))))
        if request.method == "POST":
            if table == "v3_facts":
                for r in self.rows[table]:
                    if (r["owner_id"] == data["owner_id"]
                            and r["value_hash"] == data["value_hash"]
                            and r.get("deleted_at") is None):
                        return httpx.Response(409, json={"code": "23505",
                                                       "message": "duplicate"})
            prefer = request.headers.get("Prefer", "")
            if "merge-duplicates" in prefer and table == "v3_extraction_state":
                for r in self.rows[table]:
                    if (r["owner_id"] == data["owner_id"]
                            and r["session_id"] == data["session_id"]):
                        r.update(copy.deepcopy(data))
                        return httpx.Response(200, json=[copy.deepcopy(r)])
            row = {"id": str(uuid4()), **copy.deepcopy(data)}
            if "version" not in row and table == "v3_resume_documents":
                row["version"] = 0
            self.rows.setdefault(table, []).append(row)
            return httpx.Response(201, json=[copy.deepcopy(
                self.projected([row], params.get("select"))[0])])
        if request.method == "PATCH":
            if "version" in params:
                want = params["version"]
                rows = [r for r in rows if str(r.get("version", 0)) == want[3:]]
            for r in rows:
                r.update(copy.deepcopy(data))
                if "version" in data:
                    pass  # caller sets the next version explicitly
            return httpx.Response(200, json=copy.deepcopy(
                self.projected(rows, params.get("select"))))
        if request.method == "DELETE":
            kept = [r for r in self.rows.get(table, []) if r not in rows]
            self.rows[table] = kept
            return httpx.Response(200, json=copy.deepcopy(
                self.projected(rows, params.get("select"))))
        raise AssertionError("Unexpected request")


class V3StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.client = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            headers={"apikey": "test-service-key",
                     "Authorization": "Bearer test-service-key"},
            transport=httpx.MockTransport(self.store))
        self.client_patch = patch.object(memory, "_client", self.client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.addCleanup(self.client.close)

    def owner(self, **kw):
        payload = {"id": OID, "owner_key_hash": "a" * 64,
                   "anon_id": "anon-1", "memory_enabled": True,
                   "consent_at": "2026-11-20T00:00:00+00:00",
                   "created_at": "2026-11-20T00:00:00+00:00",
                   "last_seen_at": "2026-11-20T00:00:00+00:00",
                   "deleted_at": None, **kw}
        self.store.rows["v3_owner"].append(payload)
        return payload

    def fact(self, **kw):
        row = {"id": str(uuid4()), "owner_id": OID, "kind": "skill",
               "slot_path": "skills.groups.0.items.0", "value": "Python",
               "value_hash": memory_v3.fact_value_hash(
                   kw.get("kind", "skill"), kw.get("slot_path", "skills.groups.0.items.0"),
                   kw.get("value", "Python")),
               "status": "proposed", "confidence": 0.9,
               "source_session_id": SID, "source_message_id": 12,
               "evidence": [{"session_id": SID, "msg_id": 12, "quote": "会 Python"}],
               "superseded_by": None,
               "created_at": "2026-11-20T00:00:00+00:00",
               "updated_at": "2026-11-20T00:00:00+00:00", "deleted_at": None, **kw}
        self.store.rows["v3_facts"].append(row)
        return row


class OwnerTest(V3StoreTest):
    def test_enable_owner_hashes_only_never_returns_secret(self):
        key_hash = hashlib.sha256(b"tok").hexdigest()
        rec_hash = hashlib.sha256(b"code").hexdigest()
        row = memory_v3.enable_owner("anon-1", key_hash, rec_hash)
        payload = json.loads(self.store.requests[0].content)
        self.assertEqual(payload["owner_key_hash"], key_hash)
        self.assertEqual(payload["recovery_hash"], rec_hash)
        self.assertTrue(payload["memory_enabled"])
        self.assertNotIn("owner_key_hash", row)
        self.assertNotIn("recovery_hash", row)

    def test_get_owner_by_key_hash_filters_deleted(self):
        self.owner()
        found = memory_v3.get_owner_by_key_hash("a" * 64)
        self.assertEqual(found["id"], OID)
        self.assertIsNone(memory_v3.get_owner_by_key_hash("b" * 64))
        self.assertIsNone(memory_v3.get_owner_by_key_hash("short"))
        self.owner(deleted_at="2026-11-20T01:00:00+00:00")
        self.assertEqual(len([r for r in self.store.rows["v3_owner"]]), 2)

    def test_touch_owner_best_effort_never_raises(self):
        self.owner()
        self.assertTrue(memory_v3.touch_owner(OID))
        self.assertEqual(self.store.rows["v3_owner"][0]["last_seen_at"] != "", True)
        # transport failure -> False, not an exception
        def boom(request):
            return httpx.Response(503, json={"message": "down"})
        self.client._transport = httpx.MockTransport(boom)
        self.assertFalse(memory_v3.touch_owner(OID))

    def test_delete_owner_counts_and_order(self):
        self.owner()
        self.fact(); self.fact()
        self.store.rows["v3_resume_documents"].append(
            {"id": str(uuid4()), "owner_id": OID})
        self.store.rows["v3_extraction_state"].append(
            {"owner_id": OID, "session_id": SID})
        counts = memory_v3.delete_owner(OID)
        self.assertEqual(counts, {"facts": 2, "documents": 1,
                                  "extraction_state": 1, "owner": 1})
        deleted = [r.url.path.rsplit("/", 1)[-1] for r in self.store.requests
                   if r.method == "DELETE"]
        self.assertEqual(deleted, ["v3_facts", "v3_resume_documents",
                                   "v3_extraction_state", "v3_owner"])
        with self.assertRaises(memory_v3.V3NotFound):
            memory_v3.delete_owner(OID)


class FactTest(V3StoreTest):
    def setUp(self):
        super().setUp()
        self.owner()

    def test_value_hash_normalized_json_key_order(self):
        a = memory_v3.fact_value_hash("skill", "s.a", {"x": 1, "y": 2})
        b = memory_v3.fact_value_hash("skill", "s.a", {"y": 2, "x": 1})
        self.assertEqual(a, b)
        self.assertNotEqual(a, memory_v3.fact_value_hash("skill", "s.b", {"x": 1, "y": 2}))
        self.assertNotEqual(a, memory_v3.fact_value_hash("goal", "s.a", {"x": 1, "y": 2}))

    def test_insert_fact_persists_hash_and_caps_evidence(self):
        ev = [{"session_id": SID, "msg_id": i, "quote": f"q{i}"} for i in range(12)]
        row, created = memory_v3.insert_fact(
            OID, "skill", "skills.groups.0.items.0", "Python",
            confidence=0.9, source_session_id=SID, source_message_id=12, evidence=ev)
        self.assertTrue(created)
        self.assertEqual(row["value_hash"],
                         memory_v3.fact_value_hash("skill", "skills.groups.0.items.0", "Python"))
        self.assertEqual(len(row["evidence"]), 8)

    def test_insert_fact_duplicate_merges_evidence_not_new_row(self):
        ev = [{"session_id": SID, "msg_id": 1, "quote": "会 Python"}]
        first, c1 = memory_v3.insert_fact(OID, "skill", "s.a", "Python", evidence=ev)
        second, c2 = memory_v3.insert_fact(
            OID, "skill", "s.a", "Python",
            evidence=[{"session_id": SID, "msg_id": 2, "quote": "写了三年 Python"}])
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.store.rows["v3_facts"]), 1)
        self.assertEqual(len(second["evidence"]), 2)

    def test_list_facts_filters_and_keyset_cursor(self):
        for i in range(3):
            self.fact(created_at=f"2026-11-20T00:00:0{i}+00:00")
        page1 = memory_v3.list_facts(OID, limit=2)
        self.assertEqual(len(page1["facts"]), 2)
        self.assertIsNotNone(page1["next_cursor"])
        page2 = memory_v3.list_facts(OID, cursor=page1["next_cursor"])
        self.assertEqual(len(page2["facts"]), 1)
        self.assertIsNone(page2["next_cursor"])
        only = memory_v3.list_facts(OID, status="confirmed")
        self.assertEqual(only["facts"], [])
        with self.assertRaises(ValueError):
            memory_v3.list_facts(OID, status="nonsense")

    def test_set_fact_status_owner_scoped_404(self):
        row = self.fact()
        updated = memory_v3.set_fact_status(OID, row["id"], "confirmed")
        self.assertEqual(updated["status"], "confirmed")
        with self.assertRaises(ValueError):
            memory_v3.set_fact_status(OID, row["id"], "superseded")
        with self.assertRaises(memory_v3.V3NotFound):
            memory_v3.set_fact_status(OID, str(uuid4()), "confirmed")
        # wrong owner sees nothing
        with self.assertRaises(memory_v3.V3NotFound):
            memory_v3.set_fact_status(str(uuid4()), row["id"], "rejected")

    def test_supersede_creates_new_marks_old(self):
        old = self.fact(status="confirmed")
        new = memory_v3.supersede_fact(OID, old["id"], "Python 进阶")
        self.assertNotEqual(new["id"], old["id"])
        self.assertEqual(new["value"], "Python 进阶")
        self.assertEqual(new["status"], "confirmed")
        stored_old = self.store.rows["v3_facts"][0]
        self.assertEqual(stored_old["status"], "superseded")
        self.assertEqual(stored_old["superseded_by"], new["id"])
        with self.assertRaises(memory_v3.V3NotFound):
            memory_v3.supersede_fact(OID, str(uuid4()), "x")

    def test_soft_delete_fact_hides_from_lists(self):
        row = self.fact()
        self.assertTrue(memory_v3.soft_delete_fact(OID, row["id"]))
        self.assertIsNotNone(self.store.rows["v3_facts"][0]["deleted_at"])
        self.assertEqual(memory_v3.list_facts(OID)["facts"], [])
        self.assertIsNone(memory_v3.get_fact(OID, row["id"]))
        self.assertFalse(memory_v3.soft_delete_fact(OID, row["id"]))

    def test_detach_session_strips_pointer_and_that_sessions_evidence(self):
        row = self.fact(evidence=[
            {"session_id": SID, "msg_id": 1, "quote": "a"},
            {"session_id": "other", "msg_id": 2, "quote": "b"}])
        count = memory_v3.detach_session_facts(OID, SID)
        self.assertEqual(count, 1)
        stored = self.store.rows["v3_facts"][0]
        self.assertIsNone(stored["source_session_id"])
        self.assertIsNone(stored["source_message_id"])
        self.assertEqual(stored["evidence"], [{"session_id": "other",
                                               "msg_id": 2, "quote": "b"}])

    def test_fact_counts_by_kind_and_status(self):
        self.fact(kind="skill", status="confirmed")
        self.fact(kind="skill", status="proposed")
        self.fact(kind="goal", status="confirmed")
        counts = memory_v3.fact_counts(OID)
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["by_kind"], {"skill": 2, "goal": 1})
        self.assertEqual(counts["by_status"], {"confirmed": 2, "proposed": 1})


class ExtractionStateTest(V3StoreTest):
    def setUp(self):
        super().setUp()
        self.owner()

    def test_watermark_upsert_and_error_attempts(self):
        row = memory_v3.upsert_watermark(OID, SID, 42)
        self.assertEqual(row["last_message_id"], 42)
        self.assertEqual(row["attempts"], 0)
        # second upsert merges, not duplicates
        row2 = memory_v3.upsert_watermark(OID, SID, 50)
        self.assertEqual(row2["last_message_id"], 50)
        self.assertEqual(len(self.store.rows["v3_extraction_state"]), 1)
        err = memory_v3.mark_extraction_error(OID, SID, "MODEL_FAILED")
        self.assertEqual(err["attempts"], 1)
        self.assertEqual(err["last_message_id"], 50)
        self.assertEqual(memory_v3.last_extracted_at(OID) is not None, True)


class DocumentTest(V3StoreTest):
    def setUp(self):
        super().setUp()
        self.owner()

    def doc(self, **kw):
        return memory_v3.create_document(
            OID, title=kw.pop("title", "后端开发-暑期实习"),
            template_version="tpl-v1", **kw)

    def test_create_defaults_and_validation(self):
        doc = self.doc()
        self.assertEqual(doc["status"], "draft")
        self.assertEqual(doc["fields"], {})
        with self.assertRaises(ValueError):
            memory_v3.create_document(OID, title="", template_version="tpl-v1")
        with self.assertRaises(ValueError):
            memory_v3.create_document(OID, title="x", template_version="tpl-v1",
                                      status="published")

    def test_patch_document_cas_conflict(self):
        doc = self.doc()
        patched = memory_v3.patch_document(OID, doc["id"], {"title": "产品经理"},
                                           expected_version=0)
        self.assertEqual(patched["version"], 1)
        with self.assertRaises(memory_v3.V3Conflict):
            memory_v3.patch_document(OID, doc["id"], {"title": "stale write"},
                                     expected_version=0)

    def test_soft_delete_document_owner_scoped(self):
        doc = self.doc()
        self.assertTrue(memory_v3.soft_delete_document(OID, doc["id"]))
        self.assertIsNone(memory_v3.get_document(OID, doc["id"]))
        self.assertEqual(memory_v3.list_documents(OID), [])
        self.assertFalse(memory_v3.soft_delete_document(str(uuid4()), doc["id"]))


class SnapshotExportTest(V3StoreTest):
    def setUp(self):
        super().setUp()
        self.owner()

    def test_stale_sources_updated_and_deleted(self):
        f1 = self.fact(value_hash="h1")
        f2 = self.fact(value_hash="h2")
        doc = {"fact_snapshot": [{"fact_id": f1["id"], "value_hash": "h1"},
                                 {"fact_id": f2["id"], "value_hash": "h2"}],
               "evidence_map": {"a.b": [{"fact_id": f1["id"], "quote": "q"}],
                                "c.d": [{"fact_id": f2["id"], "quote": "q"}]}}
        # unchanged -> no stale
        self.assertEqual(memory_v3.stale_sources(doc, [f1, f2]), [])
        # updated value_hash -> 'updated'
        f1_new = dict(f1, value_hash="h1x")
        self.assertEqual(memory_v3.stale_sources(doc, [f1_new, f2]),
                         [{"field_path": "a.b", "fact_id": f1["id"], "change": "updated"}])
        # missing fact -> 'deleted'
        self.assertEqual(memory_v3.stale_sources(doc, [f1]),
                         [{"field_path": "c.d", "fact_id": f2["id"], "change": "deleted"}])

    def test_export_owner_full_dump_shape(self):
        self.fact()
        memory_v3.create_document(OID, title="t", template_version="v1")
        bundle = memory_v3.export_owner(OID)
        self.assertEqual(bundle["schema_version"], 1)
        self.assertEqual(bundle["owner"]["id"], OID)
        self.assertNotIn("owner_key_hash", bundle["owner"])
        self.assertEqual(len(bundle["facts"]), 1)
        self.assertEqual(len(bundle["documents"]), 1)


if __name__ == "__main__":
    unittest.main()

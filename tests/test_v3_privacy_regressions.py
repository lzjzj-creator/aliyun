"""Offline privacy regressions; capped REST pages use real filters/offsets."""
import copy
import json
import unittest
from uuid import uuid4

import httpx

from tests.test_memory_v3 import OID, SID, Store, V3StoreTest, memory_v3

OTHER_SESSION = "22222222-2222-4222-8222-222222222222"
OTHER_OWNER = "33333333-3333-4333-8333-333333333333"


class CappedStore(Store):
    """Apply a server max-rows cap, but retain Store's filters/order/offset.

    Unsupported filters fail rather than silently pretending to enforce them.
    The small cap deliberately differs from the client's requested page size.
    """
    def handle(self, request):
        for field, value in request.url.params.items():
            if field not in ("select", "order", "limit", "offset", "or"):
                assert (value.startswith(("eq.", "in.("))
                        or value in ("is.null", "not.is.null")), (field, value)
        if request.method == "GET":
            params = dict(request.url.params)
            params["limit"] = str(min(int(params.get("limit", 1000)), 2))
            request = httpx.Request(request.method,
                                    request.url.copy_with(params=params),
                                    headers=request.headers, content=request.content)
        return super().handle(request)


class PrivacyRegressionTest(V3StoreTest):
    def setUp(self):
        super().setUp()
        self.store = CappedStore()
        self.client._transport = httpx.MockTransport(self.store)
        self.owner()

    @staticmethod
    def mixed_evidence():
        return [{"session_id": SID, "msg_id": 12, "quote": "forgotten quote"},
                {"session_id": OTHER_SESSION, "msg_id": 3, "quote": "kept quote"}]

    def document(self, *, owner_id=OID, deleted=False):
        return {"id": str(uuid4()), "owner_id": owner_id, "version": 1,
                "title": "keep title", "fields": {"summary": "keep content"},
                "template_version": "v1", "status": "draft",
                "evidence_map": {"mixed": self.mixed_evidence(),
                                 "only": self.mixed_evidence()[:1]},
                "deleted_at": "2026-01-01" if deleted else None}

    def test_forget_secondary_sources_null_sources_and_deleted_facts_all_pages(self):
        facts = [self.fact(source_session_id=source, source_message_id=99,
                           deleted_at="2026-01-01" if i % 2 else None,
                           evidence=self.mixed_evidence())
                 for i, source in enumerate(
                     [SID, OTHER_SESSION, None, SID, OTHER_SESSION, None, SID])]
        foreign = self.fact(owner_id=OTHER_OWNER, evidence=self.mixed_evidence())
        foreign_before = copy.deepcopy(foreign)
        untouched = self.fact(source_session_id=OTHER_SESSION,
                              evidence=self.mixed_evidence()[1:])
        untouched_before = copy.deepcopy(untouched)
        result = memory_v3.forget_session(OID, SID)
        self.assertEqual(result["facts_detached"], len(facts))
        for i, fact in enumerate(facts):
            self.assertEqual(fact["evidence"], self.mixed_evidence()[1:])
            source = [SID, OTHER_SESSION, None, SID, OTHER_SESSION, None, SID][i]
            self.assertEqual(fact["source_session_id"], None if source == SID else source)
            self.assertEqual(fact["source_message_id"], None if source == SID else 99)
            self.assertEqual(fact["value"], "Python")
        self.assertEqual(foreign, foreign_before)
        self.assertEqual(untouched, untouched_before)
        patches = [r for r in self.store.requests if r.method == "PATCH"]
        self.assertTrue(all(r.url.params["owner_id"] == f"eq.{OID}" for r in patches))
        patch_count = len(patches)
        self.assertEqual(memory_v3.forget_session(OID, SID),
                         {"facts_detached": 0, "documents_scrubbed": 0})
        self.assertEqual(len([r for r in self.store.requests if r.method == "PATCH"]),
                         patch_count)

    def test_forget_scrubs_deleted_docs_and_history_and_restore_stays_clean(self):
        docs = [self.document(deleted=i % 2 == 1) for i in range(7)]
        foreign = self.document(owner_id=OTHER_OWNER)
        self.store.rows["v3_resume_documents"] = docs + [foreign]
        versions = []
        for doc in docs + [foreign]:
            for version in range(3):
                versions.append({"id": str(uuid4()), "owner_id": doc["owner_id"],
                                 "doc_id": doc["id"], "version": version,
                                 "snapshot": {"title": "historic title",
                                              "fields": {"summary": "historic content"},
                                              "evidence_map": copy.deepcopy(doc["evidence_map"])}})
        self.store.rows["v3_document_versions"] = versions
        foreign_before = copy.deepcopy([foreign, versions[-3:]])
        result = memory_v3.forget_session(OID, SID)
        self.assertEqual(result, {"facts_detached": 0, "documents_scrubbed": 7})
        expected = {"mixed": self.mixed_evidence()[1:]}
        for doc in docs:
            self.assertEqual(doc["evidence_map"], expected)
            self.assertEqual(doc["fields"], {"summary": "keep content"})
        for version in versions[:-3]:
            self.assertEqual(version["snapshot"]["evidence_map"], expected)
            self.assertEqual(version["snapshot"]["fields"],
                             {"summary": "historic content"})
        self.assertEqual([foreign, versions[-3:]], foreign_before)
        # Same get+patch sequence used by version restore: history cannot revive quotes.
        doc = docs[0]
        old = memory_v3.get_document_version(OID, doc["id"], 0)
        restored = memory_v3.patch_document(OID, doc["id"], old["snapshot"],
                                            expected_version=doc["version"],
                                            snapshot=copy.deepcopy(doc))
        self.assertEqual(restored["evidence_map"], expected)
        exported = memory_v3.export_owner(OID)
        self.assertNotIn("forgotten quote", json.dumps(exported))
        self.assertEqual(len(exported["documents"]), 4)

    def test_export_live_facts_and_docs_across_server_capped_pages(self):
        live = [self.fact(status=status) for status in
                ("proposed", "confirmed", "rejected", "superseded", "confirmed")]
        self.fact(deleted_at="2026-01-01", value="private deleted fact")
        self.fact(owner_id=OTHER_OWNER, value="foreign fact")
        docs = [self.document() for _ in range(5)]
        self.store.rows["v3_resume_documents"] = docs + [
            self.document(deleted=True), self.document(owner_id=OTHER_OWNER)]
        exported = memory_v3.export_owner(OID)
        self.assertEqual({r["id"] for r in exported["facts"]}, {r["id"] for r in live})
        self.assertEqual({r["id"] for r in exported["documents"]}, {r["id"] for r in docs})
        self.assertNotIn("owner_key_hash", exported["owner"])
        self.assertNotIn("private deleted fact", json.dumps(exported))
        for table in ("v3_facts", "v3_resume_documents"):
            reads = [r for r in self.store.requests if r.method == "GET"
                     and r.url.path.endswith("/" + table)]
            self.assertEqual([int(r.url.params["offset"]) for r in reads], [0, 2, 4, 5])
            self.assertTrue(all(r.url.params["deleted_at"] == "is.null" for r in reads))


if __name__ == "__main__":
    unittest.main()

"""Offline v3 resume builder tests: extractive evidence, ranking, manual merge."""
import importlib
import os
import unittest
from unittest.mock import patch

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "DASHSCOPE_API_KEY": "test-key",
}):
    rb = importlib.import_module("backend.resume_builder_v3")
    mv3 = importlib.import_module("backend.memory_v3")
    rt = importlib.import_module("backend.resume_template")

TPL = rt.load()
OID = "12345678-1234-4234-8234-123456789abc"


def make_fact(fid, kind="skill", slot="skills.groups.0.items.0", value="Python",
              status="confirmed", quote="后端用的 Python FastAPI",
              confidence=0.9):
    return {"id": fid, "owner_id": OID, "kind": kind, "slot_path": slot,
            "value": value, "status": status, "confidence": confidence,
            "value_hash": mv3.fact_value_hash(kind, slot, value),
            "evidence": [{"session_id": "s1", "msg_id": 5, "quote": quote}],
            "deleted_at": None}


def model_out(fields=None, evidence_map=None, gaps=None):
    return {"fields": fields or {}, "evidence_map": evidence_map or {},
            "gaps": gaps or []}


class BuilderTest(unittest.TestCase):
    def setUp(self):
        self.facts = [make_fact("f1"), make_fact(
            "f2", kind="experience", slot="projects.0.name",
            value="二手书小程序", status="proposed", quote="做过二手书小程序")]
        self.out = model_out()
        p = patch.object(rb.llm, "chat_json", lambda *a, **k: self.out)
        p.start()
        self.addCleanup(p.stop)

    def build(self, **kw):
        return rb.build_fields_from_facts(self.facts, TPL, **kw)

    def test_happy_path_evidence_carries_status(self):
        self.out = model_out(
            fields={"skills": {"groups": [{"name": "后端", "items": ["Python"]}]},
                    "projects": [{"name": "二手书小程序"}]},
            evidence_map={"skills.groups.0.items.0": ["f1"],
                          "projects.0.name": ["f2"]})
        out = self.build()
        fields = out["fields"]
        self.assertEqual(fields["skills"]["groups"][0]["items"][0], "Python")
        ev = out["evidence_map"]
        self.assertEqual(ev["skills.groups.0.items.0"][0]["fact_status"], "confirmed")
        self.assertEqual(ev["projects.0.name"][0]["fact_status"], "proposed")
        self.assertEqual(ev["projects.0.name"][0]["quote"], "做过二手书小程序")
        self.assertEqual({s["fact_id"] for s in out["fact_snapshot"]},
                         {"f1", "f2"})

    def test_no_evidence_blanks_leaf_and_gaps(self):
        self.out = model_out(
            fields={"skills": {"groups": [{"name": "后端", "items": ["Python"]}]}})
        out = self.build()
        self.assertEqual(out["fields"]["skills"]["groups"][0]["items"][0], "")
        reasons = [g["reason"] for g in out["gaps"]]
        self.assertTrue(any("无逐字支持" in r for r in reasons))

    def test_fake_fact_id_and_nonextractive_value_rejected(self):
        self.out = model_out(
            fields={"skills": {"groups": [{"name": "后端",
                                          "items": ["Python", "不存在技能"]}]}},
            evidence_map={"skills.groups.0.items.0": ["ghost-id"],
                          "skills.groups.0.items.1": ["f1"]})   # value∉quote
        out = self.build()
        items = out["fields"]["skills"]["groups"][0]["items"]
        self.assertEqual(items, ["", ""])
        self.assertEqual(out["evidence_map"], {})
        self.assertEqual(out["fact_snapshot"], [])

    def test_value_in_quote_but_wrong_fact_still_dropped(self):
        self.facts[1]["evidence"] = [
            {"session_id": "s1", "msg_id": 9, "quote": "完全不相关的原话"}]
        self.out = model_out(
            fields={"projects": [{"name": "二手书小程序"}]},
            evidence_map={"projects.0.name": ["f2"]})
        out = self.build()
        # f2's only quote lacks the value; fallback checks fact.value itself
        self.assertEqual(out["fields"]["projects"][0]["name"], "二手书小程序")
        self.assertEqual(out["evidence_map"]["projects.0.name"][0]["quote"],
                         "二手书小程序")

    def test_required_and_section_gaps(self):
        self.out = model_out(fields={})
        out = self.build()
        reasons = {(g["field_path"], g["reason"]) for g in out["gaps"]}
        self.assertTrue(any("事实库未覆盖" in r for _, r in reasons))
        self.assertTrue(any("素材" in r for _, r in reasons))

    def test_ranking_confirmed_first_then_keyword_hits(self):
        facts = [
            make_fact("a", value="Rust", status="proposed", quote="会 Rust"),
            make_fact("b", value="Go", status="confirmed", quote="会 Go"),
            make_fact("c", value="分布式后端", status="confirmed",
                      quote="做过分布式后端"),
        ]
        ranked = rb._rank_facts(facts, "后端工程师", "需要分布式系统经验")
        self.assertEqual(ranked[0]["id"], "c")      # confirmed + 2 keyword hits
        self.assertEqual(ranked[1]["id"], "b")      # confirmed, no hits
        self.assertEqual(ranked[2]["id"], "a")      # proposed last

    def test_ranking_cap(self):
        facts = [make_fact(f"f{i}") for i in range(200)]
        self.assertEqual(len(rb._rank_facts(facts, None, None)),
                         rb.MAX_FACTS_PER_BUILD)

    def test_model_failure_raises(self):
        with patch.object(rb.llm, "chat_json", side_effect=RuntimeError("x")):
            with self.assertRaises(rb.BuildFailed):
                self.build()

    def test_merge_manual_overrides(self):
        old = {"basics": {"name": "手改名字", "email": ""},
               "_meta": {"manual_override": ["basics.name"]}}
        new = {"basics": {"name": "模型名", "email": "a@b.c"}}
        merged = rb.merge_manual_overrides(old, new)
        self.assertEqual(merged["basics"]["name"], "手改名字")
        self.assertEqual(merged["basics"]["email"], "a@b.c")
        self.assertEqual(merged["_meta"]["manual_override"], ["basics.name"])

    def test_create_document_persists(self):
        self.out = model_out(
            fields={"skills": {"groups": [{"name": "后端", "items": ["Python"]}]}},
            evidence_map={"skills.groups.0.items.0": ["f1"]})
        saved = {}
        with patch.object(rb.memory_v3, "facts_for_generation",
                          lambda oid: self.facts), \
             patch.object(rb.memory_v3, "create_document",
                          lambda oid, **kw: saved.update(kw) or {"id": "d1", **kw}):
            doc = rb.create_document(OID, title="后端简历", target_role="后端",
                                     jd_text="需要 FastAPI")
        self.assertEqual(doc["status"], "draft")
        self.assertEqual(saved["template_version"], TPL.template_version)
        self.assertIsNotNone(saved["jd_digest"])
        self.assertEqual(saved["fields"]["skills"]["groups"][0]["items"][0],
                         "Python")

    def test_create_document_version_mismatch(self):
        with self.assertRaises(ValueError):
            rb.create_document(OID, title="t", template_version="bogus-9")

    def test_regenerate_fields_keeps_manual(self):
        doc = {"owner_id": OID, "target_role": "后端",
               "fields": {"basics": {"name": "手改", "email": ""},
                          "_meta": {"manual_override": ["basics.name"]}}}
        self.out = model_out(
            fields={"basics": {"name": "新值", "email": ""}},
            evidence_map={})
        with patch.object(rb.memory_v3, "facts_for_generation",
                          lambda oid: self.facts):
            built = rb.regenerate_fields(doc, TPL)
        self.assertEqual(built["fields"]["basics"]["name"], "手改")
        with patch.object(rb.memory_v3, "facts_for_generation",
                          lambda oid: self.facts):
            built2 = rb.regenerate_fields(doc, TPL, keep_manual=False)
        self.assertEqual(built2["fields"]["basics"]["name"], "")
    def test_merge_manual_override_grows_shortened_array(self):
        old = {"projects": [{"name": "旧一"}, {"name": "手工项目"}],
               "_meta": {"manual_override": ["projects.1.name"]}}
        new = {"projects": [{"name": "模型项目"}]}
        merged = rb.merge_manual_overrides(old, new)
        self.assertEqual(merged["projects"][1]["name"], "手工项目")
        self.assertEqual(merged["_meta"]["manual_override"],
                         ["projects.1.name"])

    def test_merge_manual_override_keeps_only_leaf_in_new_array(self):
        old = {"projects": [{"name": "Alice", "bullets": ["a"]},
                           {"name": "Bob", "bullets": ["b", "手改"]},
                           {"name": "Carol"}],
               "_meta": {"manual_override": ["projects.2.name",
                                               "projects.1.bullets.1"]}}
        new = {"projects": []}
        merged = rb.merge_manual_overrides(old, new)
        self.assertEqual(merged["projects"][2], {"name": "Carol"})
        self.assertEqual(merged["projects"][1],
                         {"bullets": ["", "手改"]})

    def test_manual_evidence_filters_unused_fact_snapshot(self):
        built = {"evidence_map": {
                     "basics.name": [{"fact_id": "f1"}],
                     "projects.0.name": [{"fact_id": "f2"}]},
                 "gaps": [],
                 "fact_snapshot": [{"fact_id": "f1", "value_hash": "a"},
                                   {"fact_id": "f2", "value_hash": "b"}]}
        fields = {"_meta": {"manual_override": ["basics.name"]}}
        out = rb.clear_manual_evidence(built, fields)
        self.assertEqual(out["evidence_map"],
                         {"projects.0.name": [{"fact_id": "f2"}]})
        self.assertEqual(out["fact_snapshot"],
                         [{"fact_id": "f2", "value_hash": "b"}])

        doc = {"owner_id": OID, "target_role": "后端",
               "fields": {"basics": {"name": "手改", "email": ""},
                           "_meta": {"manual_override": ["basics.name"]}}}
        self.out = model_out(
            fields={"basics": {"name": "模型名", "email": ""}},
            evidence_map={"basics.name": ["f1"]})
        with patch.object(rb.memory_v3, "facts_for_generation",
                          lambda oid: self.facts):
            built = rb.regenerate_fields(doc, TPL)
        self.assertEqual(built["evidence_map"], {})
        self.assertEqual(built["fields"]["basics"]["name"], "手改")
        self.assertTrue(any("手工填写" in g["reason"]
                            for g in built["gaps"]))


if __name__ == "__main__":
    unittest.main()

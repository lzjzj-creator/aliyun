"""Offline v3 fact-extractor tests: hard validation, conflicts, watermark, blocked."""
import importlib
import os
import unittest
from unittest.mock import patch

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
    "DASHSCOPE_API_KEY": "test-key",
}):
    extractor = importlib.import_module("backend.fact_extractor")
    memory = importlib.import_module("backend.memory")
    memory_v3 = importlib.import_module("backend.memory_v3")

OID = "12345678-1234-4234-8234-123456789abc"
SID = "87654321-4321-4321-8321-cba987654321"


class FakeV3:
    """In-memory stand-in for memory_v3 primitives (dedup semantics kept)."""
    def __init__(self, facts=None, state=None, owner=None):
        self.owner = {"id": OID, "memory_enabled": True} if owner is None else owner
        self.facts = list(facts or [])
        self.state = state or {}
        self.watermarks = []
        self.errors = []

    def get_owner(self, oid):
        return self.owner

    def get_extraction_state(self, oid, sid):
        return self.state

    def upsert_watermark(self, oid, sid, mid):
        self.watermarks.append(mid)
        return {}

    def mark_extraction_error(self, oid, sid, code):
        self.errors.append(code)
        return {}

    def facts_for_generation(self, oid):
        return [f for f in self.facts if f["status"] in ("confirmed", "proposed")]

    def get_fact_by_hash(self, oid, vhash):
        import copy
        return copy.deepcopy(next((f for f in self.facts
                     if f["value_hash"] == vhash and f.get("deleted_at") is None), None))

    def insert_fact(self, oid, kind, slot, value, **kw):
        vhash = memory_v3.fact_value_hash(kind, slot, value)
        old = next((f for f in self.facts
                    if f["value_hash"] == vhash and f.get("deleted_at") is None), None)
        if old:
            seen = {(e.get("session_id"), e.get("msg_id"), e.get("quote"))
                    for e in old["evidence"]}
            for e in kw.get("evidence") or []:
                key = (e["session_id"], e["msg_id"], e["quote"])
                if key not in seen:
                    old["evidence"].append(e)
            return old, False
        row = {"id": f"fact-{len(self.facts)}", "owner_id": oid, "kind": kind,
               "slot_path": slot, "value": value, "value_hash": vhash,
               "status": kw.get("status", "proposed"),
               "confidence": kw.get("confidence", 0),
               "source_session_id": kw.get("source_session_id"),
               "source_message_id": kw.get("source_message_id"),
               "evidence": kw.get("evidence") or [], "deleted_at": None}
        self.facts.append(row)
        return row, True


class ExtractorTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeV3()
        self.messages = [
            {"id": 3, "role": "user", "content": "我叫张明，在浙江大学读计算机"},
            {"id": 4, "role": "assistant", "content": "那你做过什么项目？"},
            {"id": 5, "role": "user", "content": "做过校园二手书小程序，后端用的 Python FastAPI"},
        ]
        self.output = {"facts": [], "conflicts": [], "denied": []}
        self.patches = [
            patch.object(extractor.memory_v3, "get_owner", self.fake.get_owner),
            patch.object(extractor.memory_v3, "get_extraction_state",
                         self.fake.get_extraction_state),
            patch.object(extractor.memory_v3, "upsert_watermark",
                         self.fake.upsert_watermark),
            patch.object(extractor.memory_v3, "mark_extraction_error",
                         self.fake.mark_extraction_error),
            patch.object(extractor.memory_v3, "facts_for_generation",
                         self.fake.facts_for_generation),
            patch.object(extractor.memory_v3, "get_fact_by_hash",
                         self.fake.get_fact_by_hash),
            patch.object(extractor.memory_v3, "insert_fact", self.fake.insert_fact),
            patch.object(extractor.memory, "get_session_bundle",
                         lambda sid: {"session": {"id": sid},
                                      "messages": self.messages, "profile": {}}),
            patch.object(extractor.llm, "chat_json",
                         lambda *a, **k: self.output),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def fact(self, **kw):
        base = {"kind": "skill", "slot_path": "skills.groups.0.items.0",
                "value": "Python", "msg_id": 5,
                "quote": "后端用的 Python FastAPI", "confidence": 0.9}
        base.update(kw)
        return base

    def go(self):
        return extractor.extract_incremental(OID, SID)

    def test_happy_path_statuses_and_watermark(self):
        self.output["facts"] = [
            self.fact(),
            self.fact(kind="experience", slot_path="projects.0.name",
                      value="校园二手书小程序", quote="做过校园二手书小程序")]
        out = self.go()
        self.assertEqual(out["added"], 2)
        self.assertEqual(self.fake.facts[0]["status"], "confirmed")   # skill auto
        self.assertEqual(self.fake.facts[1]["status"], "proposed")    # experience
        self.assertEqual(self.fake.watermarks, [5])
        self.assertEqual(self.fake.facts[0]["evidence"],
                         [{"session_id": SID, "msg_id": 5,
                           "quote": "后端用的 Python FastAPI"}])

    def test_msg_id_must_be_fed_user_message(self):
        self.output["facts"] = [
            self.fact(msg_id=4, quote="那你做过什么项目"),     # assistant id
            self.fact(msg_id=99),                             # fabricated id
            self.fact(msg_id=1),                              # pre-watermark id
        ]
        self.fake.state = {"last_message_id": 0}
        out = self.go()
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["skipped"], 3)

    def test_quote_must_be_verbatim_substring_of_that_message(self):
        self.output["facts"] = [
            self.fact(quote="后端用的Python"),                # 少了空格，非逐字
            self.fact(quote="Python FastAPI 后端"),           # 词序变了
            self.fact(msg_id=3, quote="后端用的 Python"),     # quote 在别的消息
        ]
        out = self.go()
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["skipped"], 3)

    def test_value_must_appear_in_quote(self):
        self.output["facts"] = [
            self.fact(value="Java"),                          # not in quote
            self.fact(value=""),                              # blank
        ]
        out = self.go()
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["skipped"], 2)

    def test_kind_and_slot_whitelist(self):
        self.output["facts"] = [
            self.fact(kind="salary"),                         # bad kind
            self.fact(slot_path="basics.unknown"),            # unknown field
            self.fact(slot_path="random.path"),               # unknown section
            self.fact(slot_path="projects.x.name"),           # non-numeric index
            self.fact(slot_path="projects.0.name"),           # valid entries path
        ]
        out = self.go()
        self.assertEqual(out["added"], 1)
        self.assertEqual(out["skipped"], 4)

    def test_bounds_and_confidence(self):
        self.output["facts"] = [
            self.fact(value="x" * 513, quote="x" * 513 + "后端用的 Python FastAPI",
                      msg_id=5),                              # oversized value
            self.fact(confidence=1.5),                        # out of range
            self.fact(confidence="high"),                     # wrong type
        ]
        out = self.go()
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["skipped"], 3)

    def test_sensitive_denylist(self):
        for text in ["我的身份证号是 330102200501011234",
                     "银行卡号 6222021234567890123 记下来",
                     "政治面貌是中共党员",
                     "去年确诊了抑郁症"]:
            self.messages = [{"id": 7, "role": "user", "content": text}]
            self.output = {"facts": [self.fact(msg_id=7, quote=text, value=text[:8])],
                           "conflicts": [], "denied": []}
            with patch.object(extractor.llm, "chat_json", lambda *a, **k: self.output):
                out = self.go()
            self.assertEqual(out["added"], 0, text)
            self.assertEqual(out["skipped"], 1, text)

    def test_dedup_merges_evidence_and_exact_dup_skips(self):
        self.output["facts"] = [self.fact()]
        out1 = self.go()
        self.assertEqual(out1["added"], 1)
        # same fact, new source quote -> merged
        self.messages = [{"id": 9, "role": "user",
                          "content": "再说一次，后端用的 Python FastAPI"}]
        self.output["facts"] = [self.fact(msg_id=9, quote="后端用的 Python FastAPI")]
        out2 = self.go()
        self.assertEqual(out2["merged"], 1)
        self.assertEqual(len(self.fake.facts[0]["evidence"]), 2)
        # identical re-extract (same session+msg+quote evidence) -> skipped
        self.fake.state = {"last_message_id": 0}
        self.messages = [{"id": 9, "role": "user",
                          "content": "再说一次，后端用的 Python FastAPI"}]
        self.output["facts"] = [self.fact(msg_id=9, quote="后端用的 Python FastAPI")]
        out3 = self.go()
        self.assertEqual(out3["skipped"], 1)
        self.assertEqual(len(self.fake.facts), 1)

    def test_confirmed_conflict_never_overwritten(self):
        old = {"id": "old", "kind": "skill", "slot_path": "skills.groups.0.items.0",
               "value": "Java", "status": "confirmed",
               "value_hash": memory_v3.fact_value_hash("skill", "skills.groups.0.items.0", "Java"),
               "evidence": [{"session_id": SID, "msg_id": 1, "quote": "会 Java"}],
               "deleted_at": None}
        self.fake.facts.append(old)
        self.output["facts"] = [self.fact(value="Python")]
        out = self.go()
        self.assertEqual(len(out["conflicts"]), 1)
        self.assertEqual(out["conflicts"][0]["slot_path"], "skills.groups.0.items.0")
        new = self.fake.facts[1]
        self.assertEqual(new["status"], "proposed")          # human decides
        self.assertEqual(self.fake.facts[0]["value"], "Java")

    def test_rejected_twin_not_revived(self):
        old = {"id": "old", "kind": "skill", "slot_path": "skills.groups.0.items.0",
               "value": "Python", "status": "rejected",
               "value_hash": memory_v3.fact_value_hash(
                   "skill", "skills.groups.0.items.0", "Python"),
               "evidence": [], "deleted_at": None}
        self.fake.facts.append(old)
        self.output["facts"] = [self.fact()]
        out = self.go()
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(self.fake.facts[0]["evidence"], [])

    def test_no_new_user_messages_early_return(self):
        self.messages = [{"id": 8, "role": "assistant", "content": "ok"}]
        self.fake.state = {"last_message_id": 5}
        out = self.go()
        self.assertEqual(out, {"added": 0, "merged": 0, "conflicts": [],
                               "skipped": 0, "denied": 0})
        self.assertEqual(self.fake.watermarks, [8])

    def test_blocked_after_three_attempts(self):
        self.fake.state = {"attempts": 3, "last_message_id": 0}
        out = self.go()
        self.assertTrue(out.get("blocked"))
        self.assertEqual(self.fake.facts, [])
        self.assertEqual(self.fake.watermarks, [])

    def test_model_failure_marks_error_and_raises(self):
        with patch.object(extractor.llm, "chat_json",
                          side_effect=RuntimeError("boom")):
            with self.assertRaises(extractor.ExtractionFailed):
                self.go()
        self.assertEqual(self.fake.errors, ["MODEL_FAILED"])

    def test_round_cap_twenty_and_prompt_slot_summary(self):
        self.fake.facts.append(
            {"id": "c1", "kind": "skill", "slot_path": "skills.groups.0.items.0",
             "value": "SECRET_SHOULD_NOT_LEAK", "status": "confirmed",
             "value_hash": "x" * 64, "evidence": [], "deleted_at": None})
        self.output["facts"] = [self.fact(msg_id=5) for _ in range(25)]
        captured = {}
        def spy(msgs, **kw):
            captured["prompt"] = msgs[0]["content"]
            return self.output
        with patch.object(extractor.llm, "chat_json", spy):
            out = self.go()
        self.assertLessEqual(out["added"], 20)
        self.assertIn("skills.groups.0.items.0", captured["prompt"])
        self.assertNotIn("SECRET_SHOULD_NOT_LEAK", captured["prompt"])


if __name__ == "__main__":
    unittest.main()

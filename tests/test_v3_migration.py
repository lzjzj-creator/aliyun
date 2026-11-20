"""Static v3 migration safety/contract tests; NOT a live PostgreSQL migration test."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


class V3MigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forward = (ROOT / "20261119_v3_memory.sql").read_text(encoding="utf-8")
        cls.rollback = (ROOT / "rollback" / "20261119_v3_memory.sql").read_text(encoding="utf-8")

    def test_four_tables_foreign_keys_and_no_v2_v1_modifications(self):
        created = re.findall(r"create table public\.(\w+)", self.forward)
        self.assertEqual(created, ["v3_owner", "v3_facts", "v3_extraction_state",
                                   "v3_resume_documents"])
        self.assertEqual(self.forward.count(
            "references public.v3_owner(id) on delete cascade"), 3)
        self.assertEqual(self.forward.count(
            "references public.chat_sessions(id) on delete cascade"), 1)
        self.assertEqual(self.forward.count(
            "references public.chat_sessions(id) on delete set null"), 1)
        self.assertNotRegex(self.forward,
            r"(?i)(?:alter|drop|insert into|update)\s+(?:public\.)?"
            r"(?:chat_sessions|chat_messages|user_profiles|documents|chunks)\b")

    def test_fact_state_machine_dedup_and_schema_checks(self):
        for fragment in [
            "'identity','goal','education','experience','skill','award','metric','constraint'",
            "status in ('proposed','confirmed','rejected','superseded')",
            "confidence between 0 and 1",
            "length(slot_path) between 1 and 120",
            "length(value_hash) = 64",
            "length(owner_key_hash) = 64",
            "recovery_hash is null or length(recovery_hash) = 64",
            "jsonb_typeof(evidence) = 'array'",
            "jsonb_typeof(fields) = 'object'",
            "jsonb_typeof(evidence_map) = 'object'",
            "jsonb_typeof(gaps) = 'array'",
            "jsonb_typeof(fact_snapshot) = 'array'",
            "status in ('draft','ready','archived')",
            "version >= 0",
            "superseded_by uuid references public.v3_facts(id)",
        ]:
            self.assertIn(fragment, self.forward)
        self.assertIn("create unique index v3_facts_dedup_idx", self.forward)
        self.assertIn("on public.v3_facts(owner_id, value_hash) where deleted_at is null",
                      self.forward)
        self.assertIn("v3_facts_slot_idx", self.forward)
        self.assertIn("v3_resume_owner_idx", self.forward)

    def test_watermark_and_updated_at_triggers(self):
        self.assertIn("primary key (owner_id, session_id)", self.forward)
        self.assertIn("last_message_id bigint not null default 0", self.forward)
        self.assertEqual(self.forward.count("before update on public."), 2)
        self.assertEqual(
            self.forward.count("execute function public.v2_touch_updated_at()"), 2)
        self.assertNotIn("create function", self.forward.lower())

    def test_service_only_rls_and_no_new_policies(self):
        for table in ["v3_owner", "v3_facts", "v3_extraction_state",
                      "v3_resume_documents"]:
            self.assertIn(
                f"alter table public.{table} enable row level security", self.forward)
        self.assertIn("from public, anon, authenticated", self.forward)
        self.assertIn("grant select, insert, update, delete", self.forward)
        self.assertIn("to service_role", self.forward)
        self.assertNotIn("create policy", self.forward.lower())
        self.assertNotIn("pgvector", self.forward.lower())
        self.assertNotIn("vector(", self.forward.lower())

    def test_rollback_is_manual_and_limited_to_v3(self):
        self.assertTrue((ROOT / "rollback" / "20261119_v3_memory.sql").is_file())
        self.assertEqual(re.findall(r"drop table if exists public\.(\w+)", self.rollback),
                         ["v3_resume_documents", "v3_extraction_state",
                          "v3_facts", "v3_owner"])
        self.assertNotIn("cascade", self.rollback.lower())
        self.assertNotRegex(self.rollback,
            r"(?i)\b(?:chat_sessions|chat_messages|user_profiles|documents|chunks)\b")
        self.assertNotIn("v2_touch_updated_at()", self.rollback)
        for sql in [self.forward, self.rollback]:
            self.assertIn("begin;", sql)
            self.assertTrue(sql.rstrip().endswith("commit;"))


if __name__ == "__main__":
    unittest.main()

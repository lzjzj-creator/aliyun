"""Static migration safety/contract tests; NOT a live PostgreSQL migration test."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


class V2MigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forward = (ROOT / "20261117_v2_memory.sql").read_text(encoding="utf-8")
        cls.rollback = (ROOT / "rollback" / "20261117_v2_memory.sql").read_text(encoding="utf-8")

    def test_three_tables_foreign_keys_and_no_existing_kb_modifications(self):
        created = re.findall(r"create table public\.(\w+)", self.forward)
        self.assertEqual(created, ["chat_sessions", "chat_messages", "user_profiles"])
        self.assertEqual(self.forward.count("references public.chat_sessions(id) on delete cascade"), 2)
        self.assertNotRegex(self.forward, r"(?i)(?:alter|drop|insert into|update)\s+(?:public\.)?(?:documents|chunks)\b")

    def test_schema_validation_and_idempotency_index(self):
        for fragment in [
            "session_token_hash ~ '^[0-9a-f]{64}$'",
            "stage in ('greeting','probing','wrapping','done')",
            "role in ('user','assistant','system')",
            "status in ('streaming','done','error')",
            "coverage between 0 and 1", "version >= 0",
            "jsonb_typeof(profile) = 'object'", "jsonb_typeof(steps) = 'array'",
            "where client_msg_id is not null and role = 'user'",
        ]:
            self.assertIn(fragment, self.forward)
        session_ddl = self.forward.split("create table public.chat_messages")[0]
        self.assertNotIn("coverage", session_ddl)

    def test_atomic_profile_creation_activity_and_two_updated_at_triggers(self):
        self.assertIn("chat_sessions_profile after insert", self.forward)
        self.assertIn("insert into public.user_profiles(session_id) values (new.id)", self.forward)
        self.assertIn("chat_messages_activity after insert", self.forward)
        self.assertIn("last_message_at = greatest(last_message_at, clock_timestamp())", self.forward)
        self.assertIn("then left(new.content, 20) else title end", self.forward)
        self.assertEqual(self.forward.count("before update on public."), 2)
        self.assertNotRegex(self.forward, r"(?i)raise\s+(?:notice|log|warning)")

    def test_service_only_rls_and_rpc_privileges(self):
        for table in ["chat_sessions", "chat_messages", "user_profiles"]:
            self.assertIn(f"alter table public.{table} enable row level security", self.forward)
        self.assertIn("from public, anon, authenticated", self.forward)
        self.assertIn("grant select, insert, update, delete", self.forward)
        self.assertNotIn("create policy", self.forward.lower())
        self.assertIn("security invoker", self.forward)
        self.assertIn("grant execute on function public.cleanup_expired_sessions(integer, boolean) to service_role", self.forward)
        self.assertIn("revoke all on sequence public.chat_messages_id_seq", self.forward)

    def test_retention_dry_run_and_scheduler_instructions(self):
        for fragment in ["days integer default 90", "dry_run boolean default true",
                         "days is null or days < 90 or dry_run is null",
                         "if dry_run then", "select count(*) into affected",
                         "get diagnostics affected = row_count", "Daily FC scheduler"]:
            self.assertIn(fragment, self.forward)
        self.assertNotIn("cron.schedule(", self.forward)

    def test_rollback_is_manual_and_limited_to_v2(self):
        self.assertTrue((ROOT / "rollback" / "20261117_v2_memory.sql").is_file())
        self.assertEqual(re.findall(r"drop table if exists public\.(\w+)", self.rollback),
                         ["chat_messages", "user_profiles", "chat_sessions"])
        self.assertNotIn("cascade", self.rollback.lower())
        self.assertNotRegex(self.rollback, r"(?i)\b(?:documents|chunks|vector|match_chunks)\b")
        for sql in [self.forward, self.rollback]:
            self.assertIn("begin;", sql)
            self.assertTrue(sql.rstrip().endswith("commit;"))


if __name__ == "__main__":
    unittest.main()

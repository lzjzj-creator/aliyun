-- Destructive manual rollback of v3 memory ONLY; review/backup before execution.
-- Stored outside the forward migrations root so migration runners cannot auto-apply it.
-- Does not touch any v2/v1 object (including the shared v2_touch_updated_at).
begin;
drop table if exists public.v3_resume_documents;
drop table if exists public.v3_extraction_state;
drop table if exists public.v3_facts;
drop table if exists public.v3_owner;
commit;

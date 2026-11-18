-- Destructive manual rollback of v2 memory ONLY; review/backup before execution.
-- Stored outside the forward migrations root so migration runners cannot auto-apply it.
begin;
drop function if exists public.cleanup_expired_sessions(integer, boolean);
drop table if exists public.chat_messages;
drop table if exists public.user_profiles;
drop table if exists public.chat_sessions;
drop function if exists public.v2_message_activity();
drop function if exists public.v2_initialize_profile();
drop function if exists public.v2_touch_updated_at();
commit;

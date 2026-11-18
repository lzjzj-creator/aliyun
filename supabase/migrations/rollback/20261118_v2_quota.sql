-- Destructive manual rollback of v2 quota/lease objects ONLY; review/backup before execution.
-- Kept separate from 20261117_v2_memory.sql's rollback: dropping chat_sessions also cascades
-- away v2_stream_lease rows, so memory objects may be removed independently of quota objects.
begin;
drop function if exists public.v2_prune_quota(boolean);
drop function if exists public.v2_token_usage_today();
drop function if exists public.v2_charge_tokens(bigint, bigint);
drop function if exists public.v2_release_stream_lease(uuid, text);
drop function if exists public.v2_acquire_stream_lease(uuid, text, integer);
drop function if exists public.v2_spend_quota(text, text, integer, integer);
drop table if exists public.v2_token_usage;
drop table if exists public.v2_stream_lease;
drop table if exists public.v2_quota_bucket;
commit;

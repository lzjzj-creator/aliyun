-- 回滚 20261122_v2_message_retention：恢复无保留期 schema。
-- 注意：已物理删除的过期消息无法恢复；被剥离的 v3 证据 quote 也无法恢复。
begin;

drop trigger if exists chat_messages_expiry on public.chat_messages;
drop function if exists public.v2_message_expiry();
revoke all on function public.v2_prune_expired_messages(integer, boolean)
  from public, anon, authenticated, service_role;
drop function if exists public.v2_prune_expired_messages(integer, boolean);
drop index if exists public.chat_messages_expiry_idx;
alter table public.chat_messages drop column if exists expires_at;

commit;

-- V3.1：chat_messages 48 小时保留期（按消息各自 created_at 起算，严格口径）。
-- 结构变更仅此一处：可索引的 expires_at；其余行为都在 prune RPC 内完成。
begin;

alter table public.chat_messages
  add column if not exists expires_at timestamptz;

update public.chat_messages
  set expires_at = created_at + interval '48 hours'
  where expires_at is null;

alter table public.chat_messages
  alter column expires_at set not null,
  alter column expires_at set default (now() + interval '48 hours');

create index if not exists chat_messages_expiry_idx
  on public.chat_messages(expires_at);

-- expires_at 永远等于 created_at + 48h；服务角色直写 REST 时也不可漂移。
create or replace function public.v2_message_expiry() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
begin
  new.expires_at := new.created_at + interval '48 hours';
  return new;
end;
$$;
drop trigger if exists chat_messages_expiry on public.chat_messages;
create trigger chat_messages_expiry before insert on public.chat_messages
  for each row execute function public.v2_message_expiry();

-- 到期清理（幂等、分批、多实例安全）：
--  1. SKIP LOCKED 批量认领并删除过期消息；
--  2. 无未过期消息的会话：标题重置为默认（标题来自首条消息原文，属原文残留）；
--  3. 该会话画像重置为空（画像由原文推导，原文到期则画像不续存）；
--  4. v3_facts 证据条目中引用已到期/不存在消息的 quote 剥离，标注
--     source_expired；结构化事实本身（经同意的长期记忆）保留。
create function public.v2_prune_expired_messages(
  batch_size integer default 500, dry_run boolean default true)
returns jsonb language plpgsql security invoker
set search_path = pg_catalog, public as $$
declare
  pruned bigint := 0; titles_reset bigint := 0;
  profiles_reset bigint := 0; quotes_stripped bigint := 0;
begin
  if dry_run is null then raise exception 'dry_run must be explicit'
    using errcode = '22023'; end if;
  if batch_size is null or batch_size < 1 or batch_size > 10000 then
    raise exception 'batch_size must be 1..10000' using errcode = '22023';
  end if;

  if dry_run then
    select count(*) into pruned from public.chat_messages
      where expires_at <= now();
    select count(*) into titles_reset from public.chat_sessions s
      where s.title <> '新会话' and not exists (
        select 1 from public.chat_messages m
        where m.session_id = s.id and m.expires_at > now());
    select count(*) into profiles_reset from public.user_profiles p
      where p.profile <> '{}'::jsonb and not exists (
        select 1 from public.chat_messages m
        where m.session_id = p.session_id and m.expires_at > now());
    select count(*) into quotes_stripped from public.v3_facts f
      where f.deleted_at is null and exists (
        select 1 from jsonb_array_elements(f.evidence) e
        where e ? 'quote' and (
          coalesce(e->>'msg_id','') !~ '^\d+$' or not exists (
            select 1 from public.chat_messages m
            where m.id = (e->>'msg_id')::bigint and m.expires_at > now())));
    return jsonb_build_object('dry_run', true,
      'expired_messages', pruned, 'titles_reset', titles_reset,
      'profiles_reset', profiles_reset,
      'evidence_facts_stripped', quotes_stripped);
  end if;

  delete from public.chat_messages where id in (
    select id from public.chat_messages
    where expires_at <= now() order by expires_at
    limit batch_size for update skip locked);
  get diagnostics pruned = row_count;

  update public.chat_sessions s set title = '新会话'
    where s.title <> '新会话' and not exists (
      select 1 from public.chat_messages m
      where m.session_id = s.id and m.expires_at > now());
  get diagnostics titles_reset = row_count;

  update public.user_profiles p
    set profile = '{}'::jsonb, coverage = 0, coverage_detail = '{}'::jsonb
    where p.profile <> '{}'::jsonb and not exists (
      select 1 from public.chat_messages m
      where m.session_id = p.session_id and m.expires_at > now());
  get diagnostics profiles_reset = row_count;

  update public.v3_facts f set evidence = coalesce((
    select jsonb_agg(case
      when e ? 'quote' and (
        coalesce(e->>'msg_id','') !~ '^\d+$' or not exists (
          select 1 from public.chat_messages m
          where m.id = (e->>'msg_id')::bigint and m.expires_at > now()))
      then (e - 'quote') || jsonb_build_object('source_expired', true)
      else e end)
    from jsonb_array_elements(f.evidence) e), '[]'::jsonb)
    where f.deleted_at is null and exists (
      select 1 from jsonb_array_elements(f.evidence) e
      where e ? 'quote' and (
        coalesce(e->>'msg_id','') !~ '^\d+$' or not exists (
          select 1 from public.chat_messages m
          where m.id = (e->>'msg_id')::bigint and m.expires_at > now())));
  get diagnostics quotes_stripped = row_count;

  return jsonb_build_object('dry_run', false,
    'expired_messages', pruned, 'titles_reset', titles_reset,
    'profiles_reset', profiles_reset,
    'evidence_facts_stripped', quotes_stripped);
end;
$$;

revoke all on function public.v2_prune_expired_messages(integer, boolean)
  from public, anon, authenticated;
grant execute on function public.v2_prune_expired_messages(integer, boolean)
  to service_role;

commit;

-- v2 memory only. Apply manually after review; this migration never schedules itself.
begin;

create table public.chat_sessions (
  id uuid primary key default gen_random_uuid(),
  anon_id text not null check (length(btrim(anon_id)) > 0),
  session_token_hash text not null check (session_token_hash ~ '^[0-9a-f]{64}$'),
  title text not null default '新会话',
  stage text not null default 'greeting' check (stage in ('greeting','probing','wrapping','done')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_message_at timestamptz not null default now()
);
create index chat_sessions_anon_idx on public.chat_sessions(anon_id, updated_at desc);
create index chat_sessions_expiry_idx on public.chat_sessions(last_message_at);

create table public.chat_messages (
  id bigint generated always as identity primary key,
  session_id uuid not null references public.chat_sessions(id) on delete cascade,
  client_msg_id text check (client_msg_id is null or length(btrim(client_msg_id)) > 0),
  role text not null check (role in ('user','assistant','system')),
  content text not null,
  status text not null default 'done' check (status in ('streaming','done','error')),
  steps jsonb not null default '[]' check (jsonb_typeof(steps) = 'array'),
  sources jsonb not null default '[]' check (jsonb_typeof(sources) = 'array'),
  meta jsonb not null default '{}' check (jsonb_typeof(meta) = 'object'),
  created_at timestamptz not null default now()
);
create index chat_messages_session_idx on public.chat_messages(session_id, id);
create unique index chat_messages_client_msg_idx on public.chat_messages(session_id, client_msg_id)
  where client_msg_id is not null and role = 'user';

create table public.user_profiles (
  session_id uuid primary key references public.chat_sessions(id) on delete cascade,
  profile jsonb not null default '{}' check (jsonb_typeof(profile) = 'object'),
  coverage numeric(4,3) not null default 0 check (coverage between 0 and 1),
  coverage_detail jsonb not null default '{}' check (jsonb_typeof(coverage_detail) = 'object'),
  version bigint not null default 0 check (version >= 0),
  updated_at timestamptz not null default now()
);

-- Dedicated names avoid replacing any pre-existing application's trigger function.
create function public.v2_touch_updated_at() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
begin new.updated_at = clock_timestamp(); return new; end;
$$;
create trigger chat_sessions_touch before update on public.chat_sessions
  for each row execute function public.v2_touch_updated_at();
create trigger user_profiles_touch before update on public.user_profiles
  for each row execute function public.v2_touch_updated_at();

-- Atomic session/profile creation, including direct service-role REST inserts.
create function public.v2_initialize_profile() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
begin
  insert into public.user_profiles(session_id) values (new.id);
  return new;
end;
$$;
create trigger chat_sessions_profile after insert on public.chat_sessions
  for each row execute function public.v2_initialize_profile();

-- Activity cannot drift if a REST caller fails after inserting a message.
-- No content is logged. Duplicate idempotency inserts never fire this trigger.
create function public.v2_message_activity() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
begin
  update public.chat_sessions
     set last_message_at = greatest(last_message_at, clock_timestamp()),
         title = case when new.role = 'user' and title = '新会话'
                      and not exists (select 1 from public.chat_messages
                        where session_id = new.session_id and role = 'user' and id < new.id)
                      then left(new.content, 20) else title end
   where id = new.session_id;
  return new;
end;
$$;
create trigger chat_messages_activity after insert on public.chat_messages
  for each row execute function public.v2_message_activity();

-- Service-only RPC. Daily FC scheduler should call with dry_run=false explicitly.
-- Preview: select public.cleanup_expired_sessions(90, true);
-- Execute: select public.cleanup_expired_sessions(90, false);
-- No pg_cron installation/permission assumptions or automatic live schedule.
create function public.cleanup_expired_sessions(days integer default 90, dry_run boolean default true)
returns bigint language plpgsql security invoker set search_path = pg_catalog, public as $$
declare affected bigint;
begin
  if days is null or days < 90 or dry_run is null then
    raise exception 'retention must be at least 90 days and dry_run must be explicit' using errcode = '22023';
  end if;
  if dry_run then
    select count(*) into affected from public.chat_sessions
      where last_message_at < now() - make_interval(days => days);
  else
    delete from public.chat_sessions where last_message_at < now() - make_interval(days => days);
    get diagnostics affected = row_count;
  end if;
  return affected;
end;
$$;

-- Server service key only: no browser/anonymous policies, no PUBLIC grants.
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;
alter table public.user_profiles enable row level security;
revoke all on public.chat_sessions, public.chat_messages, public.user_profiles from public, anon, authenticated;
grant select, insert, update, delete on public.chat_sessions, public.chat_messages, public.user_profiles to service_role;
revoke all on sequence public.chat_messages_id_seq from public, anon, authenticated;
grant usage, select on sequence public.chat_messages_id_seq to service_role;
revoke all on function public.cleanup_expired_sessions(integer, boolean) from public, anon, authenticated;
grant execute on function public.cleanup_expired_sessions(integer, boolean) to service_role;
revoke all on function public.v2_touch_updated_at(), public.v2_initialize_profile(), public.v2_message_activity() from public, anon, authenticated;
commit;

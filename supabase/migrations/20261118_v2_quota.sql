-- v2 跨实例配额与互斥（多 FC 实例上线前置；docs/v2-P0-本地验证.md「限流与会话互斥仅支持单实例」）。
-- 只做计数与租约，不含任何消息正文/令牌明文。Apply manually after review; never self-schedules.
begin;

-- Tumbling-window counters. window_start is bucket-aligned by the RPC, so a key
-- only ever touches one row per window and rows stay bounded.
create table public.v2_quota_bucket (
  scope text not null check (scope in ('ip', 'anon')),
  bucket_key text not null check (length(btrim(bucket_key)) > 0),
  window_start timestamptz not null,
  hits bigint not null default 0 check (hits >= 0),
  primary key (scope, bucket_key, window_start)
);
create index v2_quota_bucket_window_idx on public.v2_quota_bucket(window_start);

-- One live interview stream per session, across every FC instance.
create table public.v2_stream_lease (
  session_id uuid primary key references public.chat_sessions(id) on delete cascade,
  lease_token text not null check (lease_token ~ '^[0-9a-f]{16,64}$'),
  expires_at timestamptz not null
);
create index v2_stream_lease_expiry_idx on public.v2_stream_lease(expires_at);

-- Approximate daily token budget, shared instead of per-process.
create table public.v2_token_usage (
  day date primary key,
  used bigint not null default 0 check (used >= 0)
);

-- Admit or throttle one request. allowed=false means the window is saturated;
-- retry_after is the seconds left in the current tumbling window.
create function public.v2_spend_quota(p_scope text, p_key text, p_limit integer, p_window integer)
returns jsonb language plpgsql security invoker set search_path = pg_catalog, public as $$
declare
  wstart timestamptz;
  hits_now bigint;
  retry integer;
begin
  if p_scope is null or p_scope not in ('ip', 'anon') then
    raise exception 'unknown quota scope' using errcode = '22023';
  end if;
  if p_key is null or length(btrim(p_key)) = 0 or length(p_key) > 200 then
    raise exception 'quota key must be a bounded nonempty string' using errcode = '22023';
  end if;
  if p_limit is null or p_limit < 1 or p_limit > 1000000 then
    raise exception 'quota limit out of range' using errcode = '22023';
  end if;
  if p_window is null or p_window < 1 or p_window > 86400 then
    raise exception 'quota window out of range' using errcode = '22023';
  end if;

  wstart := to_timestamp(floor(extract(epoch from clock_timestamp()) / p_window) * p_window);
  insert into public.v2_quota_bucket(scope, bucket_key, window_start, hits)
    values (p_scope, p_key, wstart, 0) on conflict (scope, bucket_key, window_start) do nothing;
  update public.v2_quota_bucket set hits = hits + 1
    where scope = p_scope and bucket_key = p_key and window_start = wstart and hits < p_limit
    returning hits into hits_now;

  if hits_now is null then
    select hits into hits_now from public.v2_quota_bucket
      where scope = p_scope and bucket_key = p_key and window_start = wstart;
    retry := greatest(1, ceil(p_window -
      (extract(epoch from clock_timestamp()) - extract(epoch from wstart))))::integer;
    return jsonb_build_object('allowed', false, 'hits', hits_now, 'retry_after', retry);
  end if;
  return jsonb_build_object('allowed', true, 'hits', hits_now, 'retry_after', 0);
end;
$$;

-- Take (or renew) the single-stream lease for a session. Returns false when another
-- holder is still live; concurrent callers serialise on the primary key row lock.
create function public.v2_acquire_stream_lease(p_session_id uuid, p_lease_token text, p_ttl integer)
returns boolean language plpgsql security invoker set search_path = pg_catalog, public as $$
declare taken boolean;
begin
  if p_session_id is null or p_lease_token is null or p_lease_token !~ '^[0-9a-f]{16,64}$' then
    raise exception 'lease requires a session uuid and a hex token' using errcode = '22023';
  end if;
  if p_ttl is null or p_ttl < 1 or p_ttl > 900 then
    raise exception 'lease ttl must be between 1 and 900 seconds' using errcode = '22023';
  end if;
  if not exists (select 1 from public.chat_sessions where id = p_session_id) then
    return false;
  end if;
  insert into public.v2_stream_lease(session_id, lease_token, expires_at)
    values (p_session_id, p_lease_token, clock_timestamp() + make_interval(secs => p_ttl))
    on conflict (session_id) do update
      set lease_token = excluded.lease_token,
          expires_at = excluded.expires_at
      where public.v2_stream_lease.expires_at <= clock_timestamp()
         or public.v2_stream_lease.lease_token = excluded.lease_token
    returning true into taken;
  return coalesce(taken, false);
end;
$$;

-- Release only ever drops the caller's own lease, so a timed-out holder cannot
-- knock out the session's new owner.
create function public.v2_release_stream_lease(p_session_id uuid, p_lease_token text)
returns integer language plpgsql security invoker set search_path = pg_catalog, public as $$
declare affected integer;
begin
  delete from public.v2_stream_lease
    where session_id = p_session_id and lease_token = p_lease_token;
  get diagnostics affected = row_count;
  return affected;
end;
$$;

-- Add approximate token spend and report whether the day stays inside budget.
-- Overshoot is refused (allowed=false) rather than silently charged.
create function public.v2_charge_tokens(p_tokens bigint, p_budget bigint)
returns jsonb language plpgsql security invoker set search_path = pg_catalog, public as $$
declare used_now bigint;
begin
  if p_tokens is null or p_tokens < 0 then
    raise exception 'token charge must not be negative' using errcode = '22023';
  end if;
  if p_budget is null or p_budget < 1 then
    raise exception 'token budget must be positive' using errcode = '22023';
  end if;
  insert into public.v2_token_usage(day, used) values (current_date, 0) on conflict (day) do nothing;
  update public.v2_token_usage set used = used + p_tokens
    where day = current_date and used + p_tokens <= p_budget
    returning used into used_now;
  if used_now is null then
    select used into used_now from public.v2_token_usage where day = current_date;
    return jsonb_build_object('allowed', false, 'used', used_now, 'budget', p_budget);
  end if;
  return jsonb_build_object('allowed', true, 'used', used_now, 'budget', p_budget);
end;
$$;

-- Read today's spend without charging (used before a stream starts).
create function public.v2_token_usage_today()
returns bigint language sql security invoker stable set search_path = pg_catalog, public as $$
  select coalesce((select used from public.v2_token_usage where day = current_date), 0)::bigint;
$$;

-- Housekeeping for the daily scheduler; windows older than 7 days and dead leases.
create function public.v2_prune_quota(dry_run boolean default true)
returns jsonb language plpgsql security invoker set search_path = pg_catalog, public as $$
declare buckets bigint; leases bigint;
begin
  if dry_run is null then raise exception 'dry_run must be explicit' using errcode = '22023'; end if;
  if dry_run then
    select count(*) into buckets from public.v2_quota_bucket where window_start < now() - interval '7 days';
    select count(*) into leases from public.v2_stream_lease where expires_at < now();
  else
    delete from public.v2_quota_bucket where window_start < now() - interval '7 days';
    get diagnostics buckets = row_count;
    delete from public.v2_stream_lease where expires_at < now();
    get diagnostics leases = row_count;
  end if;
  return jsonb_build_object('quota_buckets', buckets, 'stream_leases', leases);
end;
$$;

-- Server service key only: no browser/anonymous policies, no PUBLIC grants.
alter table public.v2_quota_bucket enable row level security;
alter table public.v2_stream_lease enable row level security;
alter table public.v2_token_usage enable row level security;
revoke all on public.v2_quota_bucket, public.v2_stream_lease, public.v2_token_usage
  from public, anon, authenticated;
grant select, insert, update, delete on public.v2_quota_bucket, public.v2_stream_lease,
  public.v2_token_usage to service_role;
revoke all on function public.v2_spend_quota(text, text, integer, integer) from public, anon, authenticated;
revoke all on function public.v2_acquire_stream_lease(uuid, text, integer) from public, anon, authenticated;
revoke all on function public.v2_release_stream_lease(uuid, text) from public, anon, authenticated;
revoke all on function public.v2_charge_tokens(bigint, bigint) from public, anon, authenticated;
revoke all on function public.v2_token_usage_today() from public, anon, authenticated;
revoke all on function public.v2_prune_quota(boolean) from public, anon, authenticated;
grant execute on function public.v2_spend_quota(text, text, integer, integer) to service_role;
grant execute on function public.v2_acquire_stream_lease(uuid, text, integer) to service_role;
grant execute on function public.v2_release_stream_lease(uuid, text) to service_role;
grant execute on function public.v2_charge_tokens(bigint, bigint) to service_role;
grant execute on function public.v2_token_usage_today() to service_role;
grant execute on function public.v2_prune_quota(boolean) to service_role;
commit;

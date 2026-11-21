-- v3 owner 级日配额（§10）：抽取 30/天、简历生成 20/天，落库而非进程内。
-- spend 走原子 RPC：行锁下条件自增，超限返回 -1，不预扣。
begin;

create table public.v3_quota_daily (
  owner_id uuid not null references public.v3_owner(id) on delete cascade,
  day date not null,
  extracts integer not null default 0 check (extracts >= 0),
  resume_gens integer not null default 0 check (resume_gens >= 0),
  updated_at timestamptz not null default now(),
  primary key (owner_id, day)
);

alter table public.v3_quota_daily enable row level security;
-- 无 policies：与 v3 其余表一致，仅 service_role 可读写。

create function public.v3_spend_daily_quota(
  p_owner uuid, p_kind text, p_cap integer)
returns integer language plpgsql security invoker
set search_path = pg_catalog, public as $$
declare n integer;
begin
  if p_kind not in ('extract', 'resume_gen')
     or p_cap is null or p_cap < 1 then
    raise exception 'invalid quota kind or cap' using errcode = '22023';
  end if;
  insert into public.v3_quota_daily(owner_id, day)
    values (p_owner, current_date)
    on conflict (owner_id, day) do nothing;
  if p_kind = 'extract' then
    update public.v3_quota_daily
      set extracts = extracts + 1, updated_at = now()
      where owner_id = p_owner and day = current_date
        and extracts < p_cap
      returning extracts into n;
  else
    update public.v3_quota_daily
      set resume_gens = resume_gens + 1, updated_at = now()
      where owner_id = p_owner and day = current_date
        and resume_gens < p_cap
      returning resume_gens into n;
  end if;
  return coalesce(n, -1);   -- -1 = 今日额度已尽，未扣
end;
$$;

revoke all on function public.v3_spend_daily_quota(uuid, text, integer)
  from public, anon, authenticated;
grant execute on function public.v3_spend_daily_quota(uuid, text, integer)
  to service_role;

commit;

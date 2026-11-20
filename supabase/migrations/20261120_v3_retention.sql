-- v3 所有者保留期：365 天无活动硬删 owner（cascade 带走 facts/state/documents）。
-- D4 拍板口径：先 dry-run 报表核对命中数，再显式执行；函数本身不装调度器。
-- Preview: select public.v3_prune_inactive_owners(365, true);
-- Execute: select public.v3_prune_inactive_owners(365, false);
begin;

create function public.v3_prune_inactive_owners(days integer default 365, dry_run boolean default true)
returns bigint language plpgsql security invoker set search_path = pg_catalog, public as $$
declare affected bigint;
begin
  if days is null or days < 365 or dry_run is null then
    raise exception 'v3 retention must be at least 365 days and dry_run must be explicit' using errcode = '22023';
  end if;
  if dry_run then
    select count(*) into affected from public.v3_owner
      where coalesce(last_seen_at, created_at) < now() - make_interval(days => days);
  else
    delete from public.v3_owner
      where coalesce(last_seen_at, created_at) < now() - make_interval(days => days);
    get diagnostics affected = row_count;
  end if;
  return affected;
end;
$$;

revoke all on function public.v3_prune_inactive_owners(integer, boolean) from public, anon, authenticated;
grant execute on function public.v3_prune_inactive_owners(integer, boolean) to service_role;

commit;

-- v3 所有者按哈希查找走 RPC body：owner_key_hash/recovery_hash 是验证物，
-- 不应出现在 URL 查询串里（uvicorn 访问日志会留痕）。
begin;

create function public.v3_find_owner(
  p_key_hash text default null,
  p_recovery_hash text default null)
returns setof public.v3_owner language sql stable security invoker
set search_path = pg_catalog, public as $$
  select * from public.v3_owner
    where deleted_at is null
      and ((p_key_hash is not null and owner_key_hash = p_key_hash)
       or (p_recovery_hash is not null and recovery_hash = p_recovery_hash))
    limit 2;
$$;

revoke all on function public.v3_find_owner(text, text)
  from public, anon, authenticated;
grant execute on function public.v3_find_owner(text, text) to service_role;

commit;

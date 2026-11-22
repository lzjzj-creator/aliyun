begin;
revoke all on public.admin_audit from public, anon, authenticated, service_role;
drop table if exists public.admin_audit;
commit;

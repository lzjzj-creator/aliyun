-- V3.1 管理后台：管理员操作审计（无账号体系下的唯一管理审计项）。
-- 只存动作与计数类 detail，不存聊天正文/用户标识；普通用户与匿名均不可读写。
begin;

create table public.admin_audit (
  id bigint generated always as identity primary key,
  action text not null check (length(btrim(action)) between 1 and 60),
  detail jsonb not null default '{}' check (jsonb_typeof(detail) = 'object'),
  created_at timestamptz not null default now()
);
create index admin_audit_created_idx on public.admin_audit(created_at desc);

alter table public.admin_audit enable row level security;
revoke all on public.admin_audit from public, anon, authenticated;
grant select, insert on public.admin_audit to service_role;
revoke all on sequence public.admin_audit_id_seq from public, anon, authenticated;
grant usage, select on sequence public.admin_audit_id_seq to service_role;

commit;

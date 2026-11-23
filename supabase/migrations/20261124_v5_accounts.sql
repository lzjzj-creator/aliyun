-- v5 单归属根账号模型（docs/知脉-v5-账号与新手引导-开发计划.md §2）。
-- 所有用户数据挂 app_users 一行；kind=guest 为静默访客行，注册=升级同一行。
-- 同时停用全部自动保留期调度（48h 消息清除 / 90 天会话清理 / 365 天 owner 保留）。
begin;

create table public.app_users (
  id uuid primary key default gen_random_uuid(),
  kind text not null default 'guest' check (kind in ('guest','user')),
  username text unique,                       -- guest 为 null
  username_lc text unique,                    -- 小写归一，防大小写重复注册
  password_hash text,                         -- scrypt$N$r$p$salt$hex；guest 为 null
  init_profile jsonb not null default '{}' check (jsonb_typeof(init_profile) = 'object'),
  settings jsonb not null default '{}' check (jsonb_typeof(settings) = 'object'),
  guide_seen_at timestamptz,                  -- 气泡教程跨设备只播一次
  created_at timestamptz not null default now(),
  last_login_at timestamptz,
  deleted_at timestamptz
);
alter table public.app_users enable row level security;

create table public.app_user_tokens (
  token_hash text primary key check (token_hash ~ '^[0-9a-f]{64}$'),  -- sha256(auth_token)
  user_id uuid not null references public.app_users(id) on delete cascade,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '90 days',
  revoked_at timestamptz
);
create index app_user_tokens_user_idx on public.app_user_tokens(user_id);
alter table public.app_user_tokens enable row level security;

-- 简历草稿服务端化（取代 localStorage resume_draft_*）
create table public.resume_drafts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.app_users(id) on delete cascade,
  template_version text not null default '',
  title text not null default '未命名草稿',
  fields jsonb not null default '{}' check (jsonb_typeof(fields) = 'object'),
  evidence_map jsonb not null default '{}' check (jsonb_typeof(evidence_map) = 'object'),
  gaps jsonb not null default '[]' check (jsonb_typeof(gaps) = 'array'),
  photo jsonb,                              -- {data_url | asset_ref}
  slug text,                                -- 客户端自定义键（如 current/notes），uuid 为系统键
  updated_at timestamptz not null default now()
);
create index resume_drafts_user_idx on public.resume_drafts(user_id, updated_at desc);
create unique index resume_drafts_slug_idx
  on public.resume_drafts(user_id, slug) where slug is not null;
alter table public.resume_drafts enable row level security;

-- 归属列：guest/注册行直写 user_id（无认领、无迁移）
alter table public.chat_sessions
  add column if not exists user_id uuid references public.app_users(id) on delete cascade;
alter table public.v3_owner
  add column if not exists user_id uuid references public.app_users(id) on delete cascade;
create index if not exists chat_sessions_user_idx
  on public.chat_sessions(user_id) where user_id is not null;
create index if not exists v3_owner_user_idx
  on public.v3_owner(user_id) where user_id is not null;

-- 停用自动保留期调度（机制保留可逆：函数与 expires_at/retention 列不删）。
-- jobname 按实际 cron.job 核对；此处 do 块容错处理 job 不存在的情况。
do $$
declare
  j record;
begin
  for j in select jobid from cron.job
           where jobname in (
             'zhimai_v2_message_prune',     -- 48h 消息物理清除
             'zhimai_v2_session_cleanup',   -- 90 天会话清理
             'zhimai_v3_owner_retention'    -- 365 天 owner 保留
           )
  loop
    perform cron.unschedule(j.jobid);
  end loop;
exception
  when undefined_table or undefined_object then
    -- pg_cron 未装或 job 不存在：跳过（本地/新环境）
    null;
end $$;

commit;

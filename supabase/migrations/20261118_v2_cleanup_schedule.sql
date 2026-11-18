-- 可选：90 天保留期 + 配额表的每日清理定时器（数据库侧 pg_cron）。
--
-- 前置条件（两项都不满足时不要执行本文件）：
--   1. 已按顺序应用 20261117_v2_memory.sql 与 20261118_v2_quota.sql；
--   2. 项目已启用 pg_cron 扩展（Supabase：Database → Extensions → pg_cron，
--      然后在 SQL Editor 里 create extension if not exists pg_cron with schema extensions;）。
--
-- 没有 pg_cron 时的替代方案（语义等价，二选一即可）：
--   * 运维批准的 FC/外部定时器每日调用 backend.memory.cleanup_expired(90, False)
--     与 backend.memory.prune_quota(False)；
--   * 管理员登录后调用 POST /api/admin/cleanup（默认 dry_run=true 先核对行数，
--     再显式 {"days":90,"dry_run":false} 执行）。
-- 本文件不会自动安装扩展、也不会修改任何表结构；回滚见 rollback/ 同名文件。
begin;

-- 调度角色必须能执行这两个函数（函数属主/postgres 默认可执行；service_role 已在迁移中授权）。
-- 先干跑一次，确认命中行数可接受，再留下真实调度。
select public.cleanup_expired_sessions(90, true);
select public.v2_prune_quota(true);

-- 每个作业只放一条语句，避免依赖多语句隐式事务语义。
-- '0 19 * * *' 为 UTC，即北京时间次日 03:00；两条错开 10 分钟便于按 run_details 归因。
select cron.schedule('zhimai_v2_session_cleanup', '0 19 * * *',
  $job$ select public.cleanup_expired_sessions(90, false) $job$);
select cron.schedule('zhimai_v2_quota_prune', '10 19 * * *',
  $job$ select public.v2_prune_quota(false) $job$);

commit;

-- 验证触发记录（部署后第二天执行，必须看到 status='success'）：
--   select jobid, jobname, schedule, active from cron.job order by jobid;
--   select jobid, status, return_message, start_time, end_time
--     from cron.job_run_details order by start_time desc limit 10;
-- 清理是否真的生效（保留期边界，验收清单第 7 项的线上复现）：
--   select count(*) from public.chat_sessions
--     where last_message_at < now() - interval '90 days';   -- 执行清理后应为 0

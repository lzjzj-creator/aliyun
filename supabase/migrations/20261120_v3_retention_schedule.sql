-- 可选：v3 所有者 365 天保留期的每日清理定时器（数据库侧 pg_cron）。
--
-- 前置条件（两项都不满足时不要执行本文件）：
--   1. 已按顺序应用 20261119_v3_memory.sql 与 20261120_v3_retention.sql；
--   2. 项目已启用 pg_cron 扩展（Supabase：Database → Extensions → pg_cron，
--      然后在 SQL Editor 里 create extension if not exists pg_cron with schema extensions;）。
--
-- 没有 pg_cron 时的替代方案（语义等价，二选一即可）：
--   * 运维批准的 FC/外部定时器每日调用 backend.memory_v3.prune_inactive_owners(False)；
--   * 管理员人工执行 select public.v3_prune_inactive_owners(365, true) 干跑核对后
--     再 select public.v3_prune_inactive_owners(365, false)。
-- 本文件不会自动安装扩展、也不会修改任何表结构。
begin;

-- 先干跑一次，确认命中行数可接受，再留下真实调度。
select public.v3_prune_inactive_owners(365, true);

-- '20 19 * * *' UTC，即北京时间次日 03:20，与 v2 两个作业错开。
select cron.schedule('zhimai_v3_owner_retention', '20 19 * * *',
  $job$ select public.v3_prune_inactive_owners(365, false) $job$);

commit;

-- 验证触发记录（部署后第二天执行，必须看到 status='success'）：
--   select jobid, jobname, schedule, active from cron.job order by jobid;
--   select jobid, status, return_message from cron.job_run_details
--     order by start_time desc limit 10;
-- 保留期边界复现：
--   select count(*) from public.v3_owner
--     where coalesce(last_seen_at, created_at) < now() - interval '365 days';

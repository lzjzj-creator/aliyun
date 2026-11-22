-- 可选：48 小时消息保留期的 pg_cron 定时器（与 20261118_v2_cleanup_schedule 同模式）。
--
-- 前置：
--   1. 已应用 20261122_v2_message_retention.sql；
--   2. 项目已启用 pg_cron 扩展（Supabase：Database → Extensions → pg_cron）。
--
-- 没有 pg_cron 时的替代方案（语义等价，二选一即可）：
--   * 运维批准的 FC/外部定时器每 5 分钟调用 POST /api/admin/cleanup
--     （管理员入口，先 dry_run 核对）；
--   * 每批最多删 batch_size 行；到期量大时多跑几个周期自然追平。
--
-- 对用户的准确表述：消息到期即不可读；后台按每 5 分钟周期物理清除，
-- 正常情况下到期后 15 分钟内完成在线主存储清理（见 V3.1 计划 §6.2）。

begin;

-- 先干跑一次核对命中行数（计划 §10 门：清理执行前必须 dry-run 报告）。
select public.v2_prune_expired_messages(500, true);

-- '*/5 * * * *' 每 5 分钟一批，错峰于每日会话清理/配额作业。
select cron.schedule('zhimai_v2_message_prune', '*/5 * * * *',
  $job$ select public.v2_prune_expired_messages(500, false) $job$);

commit;

-- 验证（部署后执行）：
--   select jobid, jobname, schedule, active from cron.job order by jobid;
--   select status, return_message, start_time
--     from cron.job_run_details order by start_time desc limit 10;
-- 失败告警：job_run_details 连续出现 status<>'success' 即视为清扫失效。

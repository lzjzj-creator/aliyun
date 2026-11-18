-- 回滚 20261118_v2_cleanup_schedule.sql：只撤掉定时作业，不动表结构与函数。
begin;
select cron.unschedule(jobid) from cron.job where jobname in ('zhimai_v2_session_cleanup', 'zhimai_v2_quota_prune');
commit;

-- 确认已停止：select jobid, jobname, active from cron.job;

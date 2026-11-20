begin;

drop function if exists public.v3_spend_daily_quota(uuid, text, integer);
drop table if exists public.v3_quota_daily;

commit;

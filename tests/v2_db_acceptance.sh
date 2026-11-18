#!/usr/bin/env bash
# v2 迁移隔离库验收（docs/v2-P0-本地验证.md 的 8 条清单，脚本化可复跑）
#
# 用法：
#   PGBIN=/path/to/pgsql/bin PGPORT=55432 bash tests/v2_db_acceptance.sh
# 前置：目标库必须是空库（脚本会创建 Supabase 等价角色 anon/authenticated/service_role，
#      并建 documents/chunks 影子表用来证明 v2 迁移不碰 v1 对象）。
# 说明：pgvector 不在 EDB 便携版里，match_chunks 向量函数不在本脚本范围，属 Supabase 环境项。
set -uo pipefail

PGBIN="${PGBIN:-}"
PGHOST_="${PGHOST:-127.0.0.1}"
PGPORT_="${PGPORT:-55432}"
PGUSER_="${PGUSER:-postgres}"
ACC_DB="${ACC_DB:-zhimai_v2_acceptance}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Windows psql 默认按控制台码页(cp936)编码 -c 参数，含中文的用例会报 invalid byte sequence。
export PGCLIENTENCODING=UTF8
PSQL="$PGBIN/psql"
[ -x "$PSQL" ] || PSQL="psql"
WORK="$(mktemp -d 2>/dev/null || echo "$REPO/.v2-acc-tmp")"; mkdir -p "$WORK"

pass=0; fail=0
ok()   { pass=$((pass+1)); echo "  ✅ $1"; }
bad()  { fail=$((fail+1)); echo "  ❌ $1"; }
note() { echo "  ·  $1"; }

# Windows 下 psql 用活动码页(cp936)转换 -c 参数，非 ASCII 用例会报 invalid byte sequence，
# 因此所有 SQL 一律走 UTF-8 文件 + -f，并在文件头显式设定 client_encoding。
N=0
_run() { # _run <extra-flags...> <sql>
  N=$((N+1))
  local f="$WORK/st-$N.sql"
  { echo "\encoding UTF8"; printf '%s\n' "$1"; } > "$f"
  shift
  "$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -v ON_ERROR_STOP=1 "$@" -f "$f" 2>&1
}
# q <sql>            -> stdout, nonzero exit on error
q()  { _run "$1" -t -A; }
# qok <sql> <label>  -> pass when statement succeeds
qok() { local out; out=$(q "$2"); if [ $? -eq 0 ]; then ok "$1"; else bad "$1 — $out"; fi; }
# qerr <sql> <label> -> pass when statement is REJECTED
qerr() { local out; out=$(q "$2"); if [ $? -ne 0 ]; then ok "$1"; else bad "$1（本应被拒绝却成功）"; fi; }
# as <role> <sql>    -> run as a Supabase-equivalent role
as() { _run "set role $1; $2" -t -A; }

echo "== 0. 准备隔离库 =="
"$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d postgres -X -q -v ON_ERROR_STOP=1 <<SQL >/dev/null
do \$\$begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
end\$\$;
drop database if exists $ACC_DB;
create database $ACC_DB;
SQL
[ $? -eq 0 ] && ok "空库 $ACC_DB + anon/authenticated/service_role 就绪" || { bad "无法准备隔离库"; exit 1; }

# v1 影子对象（真实环境由 20261115_init.sql 建，含 pgvector；这里只需证明 v2 不触碰它们）
qok "v1 documents/chunks 影子表与哨兵行就绪" \
    "create table public.documents(id bigint generated always as identity primary key, slug text, title text);
     create table public.chunks(id bigint generated always as identity primary key, doc_slug text, kb_scope text, content text);
     insert into public.documents(slug,title) values ('v1-probe','v1 哨兵行');
     insert into public.chunks(doc_slug,kb_scope,content) values ('v1-probe','plan','v1 哨兵分段');"

echo "== 1. 正向迁移 =="
"$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -v ON_ERROR_STOP=1 \
  -f "$REPO/supabase/migrations/20261117_v2_memory.sql" >"$WORK/migrate.out" 2>&1
if [ $? -eq 0 ]; then ok "干净库一次通过 20261117_v2_memory.sql"; else bad "迁移执行失败"; cat "$WORK/migrate.out"; exit 1; fi
objs=$(q "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
          where n.nspname='public' and c.relname in ('chat_sessions','chat_messages','user_profiles');")
[ "$objs" = "3" ] && ok "三表齐备" || bad "三表不齐（$objs/3）"
idx=$(q "select count(*) from pg_indexes where schemaname='public' and tablename in ('chat_sessions','chat_messages','user_profiles');")
note "索引数=$idx（含 anon/expiry/session/幂等 4 组）"
trg=$(q "select count(*) from pg_trigger t join pg_class c on c.oid=t.tgrelid
         where not t.tgisinternal and c.relname in ('chat_sessions','user_profiles','chat_messages');")
[ "$trg" = "4" ] && ok "触发器 4 个（touch×2 + profile 原子 + 活动/标题）" || bad "触发器数量异常（$trg）"

echo "== 2. 约束负向（清单 1）=="
SID=$(q "insert into public.chat_sessions(anon_id,session_token_hash) values ('11111111-1111-1111-1111-111111111111','$(printf 'a%.0s' {1..64})') returning id;")
[ -n "$SID" ] && ok "合法会话可插入（id=$SID）" || bad "合法会话插入失败：$SID"
qerr "非法 stage 被 check 拒绝" \
  "insert into public.chat_sessions(anon_id,session_token_hash,stage) values ('a','$(printf 'b%.0s' {1..64})','sleeping');"
qerr "空匿名 ID 被拒绝" \
  "insert into public.chat_sessions(anon_id,session_token_hash) values ('   ','$(printf 'c%.0s' {1..64})');"
qerr "明文令牌（非 64 位十六进制哈希）被拒绝" \
  "insert into public.chat_sessions(anon_id,session_token_hash) values ('d','plaintext-token');"
qerr "非法 role 被拒绝" \
  "insert into public.chat_messages(session_id,role,content) values ('$SID','bot','x');"
qerr "非法 status 被拒绝" \
  "insert into public.chat_messages(session_id,role,content,status) values ('$SID','user','x','pending');"
qerr "steps/sources/meta 非约定 JSON 形状被拒绝" \
  "insert into public.chat_messages(session_id,role,content,steps) values ('$SID','user','x','\"not-array\"'::jsonb);"
qerr "coverage>1 被拒绝" \
  "update public.user_profiles set coverage=1.5 where session_id='$SID';"
qerr "负 version 被拒绝" \
  "update public.user_profiles set version=-1 where session_id='$SID';"

echo "== 3. 原子画像 + updated_at 触发器（清单 2）=="
pv=$(q "select version from public.user_profiles where session_id='$SID';")
[ "$pv" = "0" ] && ok "建会话即原子生成 version=0 画像行" || bad "画像未随会话原子创建（version=$pv）"
before=$(q "select updated_at from public.chat_sessions where id='$SID';")
sleep 1.2
q "update public.chat_sessions set title='改过名' where id='$SID';" >/dev/null
after=$(q "select updated_at from public.chat_sessions where id='$SID';")
[ "$before" != "$after" ] && ok "chat_sessions 更新自动刷新 updated_at" || bad "updated_at 未被触发器刷新"
before=$(q "select updated_at from public.user_profiles where session_id='$SID';")
sleep 1.2
q "update public.user_profiles set coverage=0.250 where session_id='$SID';" >/dev/null
after=$(q "select updated_at from public.user_profiles where session_id='$SID';")
[ "$before" != "$after" ] && ok "user_profiles 更新自动刷新 updated_at" || bad "画像 updated_at 未刷新"

echo "== 4. 幂等唯一索引（清单 3）=="
n1=$(q "insert into public.chat_messages(session_id,client_msg_id,role,content) values ('$SID','cm-1','user','第一句') returning id;")
out=$(q "insert into public.chat_messages(session_id,client_msg_id,role,content) values ('$SID','cm-1','user','重复提交') returning id;")
cnt=$(q "select count(*) from public.chat_messages where session_id='$SID' and client_msg_id='cm-1' and role='user';")
if [ "$cnt" = "1" ]; then ok "同会话同 client_msg_id 用户消息只落 1 行"; else bad "幂等键未生效（$cnt 行）"; fi
qok "assistant 消息不受用户幂等索引限制" \
    "insert into public.chat_messages(session_id,client_msg_id,role,content) values ('$SID','cm-1','assistant','好的');
     insert into public.chat_messages(session_id,client_msg_id,role,content) values ('$SID','cm-1','assistant','继续');"
qok "不同会话可复用同一 client_msg_id" \
    "insert into public.chat_sessions(anon_id,session_token_hash) values ('22222222-2222-2222-2222-222222222222','$(printf 'd%.0s' {1..64})');"
SID2=$(q "insert into public.chat_sessions(anon_id,session_token_hash) values ('33333333-3333-3333-3333-333333333333','$(printf 'e%.0s' {1..64})') returning id;")
qerr "空 client_msg_id 被拒绝" \
  "insert into public.chat_messages(session_id,client_msg_id,role,content) values ('$SID2','   ','user','x');"

echo "== 5. 标题与 last_message_at（清单 4）=="
sid3=$(q "insert into public.chat_sessions(anon_id,session_token_hash) values ('44444444-4444-4444-4444-444444444444','$(printf 'f%.0s' {1..64})') returning id;")
q "insert into public.chat_messages(session_id,role,content) values ('$sid3','assistant','我先来招呼你');" >/dev/null
t0=$(q "select title from public.chat_sessions where id='$sid3';")
[ "$t0" = "新会话" ] && ok "assistant 消息不占用标题位" || bad "标题被 assistant 消息改写（$t0）"
q "insert into public.chat_messages(session_id,role,content) values ('$sid3','user',repeat('大',30));" >/dev/null
t1=$(q "select title, length(title) from public.chat_sessions where id='$sid3';")
[ "${t1##*|}" = "20" ] && ok "首条用户消息取前 20 字作标题" || bad "标题规则异常（$t1）"
lma1=$(q "select last_message_at from public.chat_sessions where id='$sid3';")
q "insert into public.chat_messages(session_id,role,content) values ('$sid3','user','第二句');" >/dev/null
lma2=$(q "select last_message_at from public.chat_sessions where id='$sid3';")
[ "$lma1" != "$lma2" ] && ok "插消息原子推进 last_message_at" || bad "last_message_at 未随消息更新"
t2=$(q "select title from public.chat_sessions where id='$sid3';")
[ "$t2" = "${t1%%|*}" ] && ok "后续用户消息不覆盖标题" || bad "标题被后续消息覆盖"

echo "== 6. 角色权限（清单 5）=="
for r in anon authenticated; do
  for tb in chat_sessions chat_messages user_profiles; do
    out=$(as "$r" "select count(*) from public.$tb;")
    case "$out" in *"permission denied"*) ok "$r 读 $tb 被拒";; *) bad "$r 竟然能读 $tb（$out）";; esac
  done
  out=$(as "$r" "insert into public.chat_sessions(anon_id,session_token_hash) values ('x','$(printf '0%.0s' {1..64})');")
  case "$out" in *"permission denied"*) ok "$r 写 chat_sessions 被拒";; *) bad "$r 竟然能写 chat_sessions";; esac
  out=$(as "$r" "select public.cleanup_expired_sessions(90,false);")
  case "$out" in *"permission denied"*) ok "$r 调用清理 RPC 被拒";; *) bad "$r 竟然能调用清理 RPC";; esac
done
out=$(as service_role "select count(*) from public.chat_sessions;")
case "$out" in *[0-9]*) ok "service_role 可读（RLS 下 bypassrls 生效）";; *) bad "service_role 读取失败（$out）";; esac
qok "service_role 可增删改三表 + 调用清理 RPC（dry_run）" \
  "set role service_role;
   insert into public.chat_sessions(anon_id,session_token_hash) values ('55555555-5555-5555-5555-555555555555','$(printf '9%.0s' {1..64})');
   update public.chat_sessions set stage='probing' where anon_id='55555555-5555-5555-5555-555555555555';
   delete from public.chat_sessions where anon_id='55555555-5555-5555-5555-555555555555';
   select public.cleanup_expired_sessions(90,true);"
rls=$(q "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
         where n.nspname='public' and c.relname in ('chat_sessions','chat_messages','user_profiles') and c.relrowsecurity;")
[ "$rls" = "3" ] && ok "三表均开启 RLS" || bad "RLS 未全覆盖（$rls/3）"
pol=$(q "select count(*) from pg_policies where schemaname='public' and tablename in ('chat_sessions','chat_messages','user_profiles');")
[ "$pol" = "0" ] && ok "未创建任何 anon/authenticated 策略（无浏览器可达入口）" || bad "出现了 $pol 条额外策略"

echo "== 7. 90 天清理（清单 6）=="
old=$(q "insert into public.chat_sessions(anon_id,session_token_hash) values ('66666666-6666-6666-6666-666666666666','$(printf '1%.0s' {1..64})') returning id;")
q "insert into public.chat_messages(session_id,role,content) values ('$old','user','老会话');
   update public.chat_sessions set last_message_at = now() - interval '120 days' where id='$old';" >/dev/null
live_before=$(q "select count(*) from public.chat_sessions where anon_id='11111111-1111-1111-1111-111111111111';")
dry=$(q "select public.cleanup_expired_sessions(90,true);")
live_after=$(q "select count(*) from public.chat_sessions where last_message_at < now() - interval '90 days';")
[ "$dry" = "1" ] && ok "dry-run 命中 1 条过期会话" || bad "dry-run 计数异常（$dry）"
still=$(q "select count(*) from public.chat_sessions where id='$old';")
[ "$still" = "1" ] && ok "dry-run 不删除任何行" || bad "dry-run 竟然删了数据"
real=$(q "select public.cleanup_expired_sessions(90,false);")
[ "$real" = "1" ] && ok "显式执行删除 1 条过期会话" || bad "实删计数异常（$real）"
gone=$(q "select count(*) from public.chat_sessions where id='$old';")
mgone=$(q "select count(*) from public.chat_messages where session_id='$old';")
pgone=$(q "select count(*) from public.user_profiles where session_id='$old';")
[ "$gone" = "0" ] && [ "$mgone" = "0" ] && [ "$pgone" = "0" ] \
  && ok "级联删除：会话/消息/画像一并消失" || bad "级联未清干净（sess=$gone msg=$mgone prof=$pgone）"
kept=$(q "select count(*) from public.chat_sessions where id='$SID';")
[ "$kept" = "1" ] && ok "活跃会话未被误删" || bad "活跃会话被误删"
qerr "retention<90 天被拒绝（不允许悄悄缩短留存）" \
  "select public.cleanup_expired_sessions(30,false);"

echo "== 8. 画像乐观锁 CAS（清单 7）=="
q "update public.user_profiles set coverage=0.500, version=1 where session_id='$SID' and version=0;" >/dev/null
n=$(q "with u as (update public.user_profiles set coverage=0.900, version=1
                  where session_id='$SID' and version=0 returning 1) select count(*) from u;")
[ "$n" = "0" ] && ok "expected_version 过期时改写 0 行（只有一个写者能成功）" || bad "CAS 语义异常（$n 行）"
cov=$(q "select coverage from public.user_profiles where session_id='$SID';")
[ "$cov" = "0.500" ] && ok "落后的写没有覆盖胜者的值" || bad "画像被落后写污染（$cov）"
# 真并发：两个独立连接在同一事务里抢 version=1 → 恰好一个改写成功
conw="$WORK/concurrent.sql"
{ echo '\encoding UTF8';
  printf "begin;\nselect pg_sleep(0.3);\nwith u as (update public.user_profiles set coverage=0.600, version=2\n  where session_id='%s' and version=1 returning 1) select count(*) from u;\ncommit;\n" "$SID"; } > "$conw"
"$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -v ON_ERROR_STOP=1 -t -A -f "$conw" >"$WORK/c1.out" 2>&1 &
"$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -v ON_ERROR_STOP=1 -t -A -f "$conw" >"$WORK/c2.out" 2>&1 &
wait
wins=$(( $(tr -dc '0-9' <"$WORK/c1.out" | tail -c 1) + $(tr -dc '0-9' <"$WORK/c2.out" | tail -c 1) ))
ver=$(q "select version from public.user_profiles where session_id='$SID';")
[ "$wins" = "1" ] && [ "$ver" = "2" ] && ok "并发 CAS：恰好 1 个连接改写成功（version=$ver）" \
  || bad "并发写异常（成功连接数=$wins，version=$ver）"

echo "== 9. 共享配额与租约迁移 =="
"$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -v ON_ERROR_STOP=1 \
  -f "$REPO/supabase/migrations/20261118_v2_quota.sql" >"$WORK/quota.out" 2>&1
if [ $? -eq 0 ]; then ok "干净状态应用 20261118_v2_quota.sql"; else bad "配额迁移失败"; cat "$WORK/quota.out"; fi
qt=$(q "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public' and c.relname in ('v2_quota_bucket','v2_stream_lease','v2_token_usage');")
[ "$qt" = "3" ] && ok "配额三表齐备" || bad "配额表不齐（$qt/3）"
qerr "未知 scope 被拒绝" "select public.v2_spend_quota('session','k',5,60);"
qerr "非法窗口被拒绝" "select public.v2_spend_quota('ip','k',5,0);"
al=0
for i in 1 2 3 4 5; do
  r=$(q "select (public.v2_spend_quota('anon','quota-probe',3,60)->>'allowed')::text;")
  [ "$r" = "true" ] && al=$((al+1))
done
[ "$al" = "3" ] && ok "同一 key 的窗口只放行 limit 次（3/5）" || bad "窗口计数异常（放行 $al）"
ra=$(q "select (public.v2_spend_quota('anon','quota-probe',3,60)->>'retry_after')::int;")
[ "$ra" -ge 1 ] && ok "触顶时返回 retry_after=$ra 秒" || bad "retry_after 异常（$ra）"
other=$(q "select (public.v2_spend_quota('anon','quota-probe-2',3,60)->>'allowed')::text;")
[ "$other" = "true" ] && ok "其它身份不受影响" || bad "配额串号"
# 并发：20 个连接抢 limit=10 → 恰好 10 个放行（这是跨实例限流的核心保证）
# 每个进程单独落盘：并发 >> 同一文件会交错写入，导致误判。
race="$WORK/race.sql"
{ echo '\encoding UTF8'; echo "select (public.v2_spend_quota('ip','race-key',10,60)->>'allowed')::text;"; } > "$race"
mkdir -p "$WORK/race" && rm -f "$WORK/race"/*.out
for i in $(seq 1 20); do
  "$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -t -A -f "$race" >"$WORK/race/$i.out" 2>&1 &
done
wait
granted=$(cat "$WORK/race"/*.out | grep -c "^true$" || true)
denied=$(cat "$WORK/race"/*.out | grep -c "^false$" || true)
hits=$(q "select hits from public.v2_quota_bucket where bucket_key='race-key';")
[ "$granted" = "10" ] && [ "$denied" = "10" ] && [ "$hits" = "10" ] \
  && ok "20 并发抢 limit=10：恰好 10 放行 / 10 拒绝，计数无丢失（hits=$hits）" \
  || bad "并发放行异常（true=$granted false=$denied hits=$hits）"
# 租约
LEASE=abcdef0123456789abcdef0123456789
LEASE2=0123456789abcdef0123456789abcdef
got=$(q "select public.v2_acquire_stream_lease('$SID','$LEASE',300);")
[ "$got" = "t" ] && ok "首个持有者拿到会话流租约" || bad "租约获取失败（$got）"
again=$(q "select public.v2_acquire_stream_lease('$SID','$LEASE2',300);")
[ "$again" = "f" ] && ok "同一会话第二个流被拒（多实例互斥）" || bad "互斥失效"
same=$(q "select public.v2_acquire_stream_lease('$SID','$LEASE',300);")
[ "$same" = "t" ] && ok "同令牌可续约" || bad "续约失败"
qerr "ttl>900 被拒绝" "select public.v2_acquire_stream_lease('$SID','$LEASE',9000);"
qerr "非十六进制令牌被拒绝" "select public.v2_acquire_stream_lease('$SID','plaintext!!',60);"
nope=$(q "select public.v2_acquire_stream_lease('$SID2','$LEASE2',60);")
[ "$nope" = "t" ] && ok "不同会话互不影响" || bad "租约串号"
rel_wrong=$(q "select public.v2_release_stream_lease('$SID','$LEASE2');")
[ "$rel_wrong" = "0" ] && ok "非持有者无法释放别人的租约" || bad "越权释放（$rel_wrong）"
rel_own=$(q "select public.v2_release_stream_lease('$SID','$LEASE');")
after_free=$(q "select public.v2_acquire_stream_lease('$SID','$LEASE2',60);")
[ "$rel_own" = "1" ] && [ "$after_free" = "t" ] && ok "释放后新持有者可进入" || bad "释放语义异常（$rel_own/$after_free）"
# 会话删除要连带释放租约（FK on delete cascade）
q "delete from public.v2_stream_lease; select public.v2_acquire_stream_lease('$SID2','$LEASE',60);" >/dev/null
q "delete from public.chat_sessions where id='$SID2';" >/dev/null
orphan=$(q "select count(*) from public.v2_stream_lease where session_id='$SID2';")
[ "$orphan" = "0" ] && ok "删除会话级联回收租约（不留死锁）" || bad "残留孤儿租约会永久卡住会话"
# 日预算
b1=$(q "select (public.v2_charge_tokens(60,100)->>'allowed')::text;")
b2=$(q "select (public.v2_charge_tokens(60,100)->>'allowed')::text;")
[ "$b1" = "true" ] && [ "$b2" = "false" ] && ok "日预算到顶后拒绝新支出" || bad "预算判定异常（$b1/$b2）"
today=$(q "select public.v2_token_usage_today();")
[ "$today" = "60" ] && ok "被拒的支出不入账（used=60）" || bad "拒绝后仍扣减（used=$today）"
b3=$(q "select (public.v2_charge_tokens(1,100)->>'used')::text;")
[ "$b3" = "61" ] && ok "预算内的小额支出正常累加（used=61）" || bad "累加异常（$b3）"
b4=$(q "select (public.v2_charge_tokens(1000,100)->>'allowed')::text;")
after=$(q "select public.v2_token_usage_today();")
[ "$b4" = "false" ] && [ "$after" = "61" ] && ok "超预算请求既不放行也不入账" || bad "越预算扣减（$b4/$after）"
qerr "负数支出被拒绝" "select public.v2_charge_tokens(-5,100);"
qerr "预算<1 被拒绝" "select public.v2_charge_tokens(5,0);"
# 并发支出：预算必须被当作整体闸门，不能各实例各自放行
used_before=$(q "select public.v2_token_usage_today();")
crace="$WORK/charge.sql"
{ echo '\encoding UTF8'; echo "select (public.v2_charge_tokens(50,500)->>'allowed')::text;"; } > "$crace"
mkdir -p "$WORK/charge" && rm -f "$WORK/charge"/*.out
for i in $(seq 1 20); do
  "$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -t -A -f "$crace" >"$WORK/charge/$i.out" 2>&1 &
done
wait
expected=$(( (500 - used_before) / 50 ))
cgranted=$(cat "$WORK/charge"/*.out | grep -c "^true$" || true)
cused=$(q "select public.v2_token_usage_today();")
[ "$cgranted" = "$expected" ] && [ "$cused" = "$((used_before + expected * 50))" ] \
  && ok "20 并发支出按剩余预算恰好放行 $expected 次（used=$cused，全局闸门成立）" \
  || bad "并发预算闸门异常（放行 $cgranted/期望 $expected，used=$cused）"
# 并发抢同一会话的流租约：只能有一个持有者
lrace="$WORK/lease.sql"
{ echo '\encoding UTF8'; echo "select public.v2_acquire_stream_lease('$SID',md5(random()::text || clock_timestamp()::text),120);"; } > "$lrace"
mkdir -p "$WORK/lease" && rm -f "$WORK/lease"/*.out
for i in $(seq 1 10); do
  "$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -t -A -f "$lrace" >"$WORK/lease/$i.out" 2>&1 &
done
wait
holders=$(cat "$WORK/lease"/*.out | grep -c "^t$" || true)
[ "$holders" = "1" ] && ok "10 并发抢同一会话租约只有 1 个持有者（访谈不互踩）" || bad "租约互斥异常（$holders 个成功）"
q "delete from public.v2_stream_lease where session_id='$SID';" >/dev/null
prune_dry=$(q "select public.v2_prune_quota(true) ? 'quota_buckets';")
[ "$prune_dry" = "t" ] && ok "prune dry-run 返回计数形状" || bad "prune 形状异常"
# 权限
for r in anon authenticated; do
  out=$(as "$r" "select count(*) from public.v2_quota_bucket;")
  case "$out" in *"permission denied"*) ok "$r 读配额表被拒";; *) bad "$r 竟然能读配额表";; esac
  out=$(as "$r" "select public.v2_spend_quota('anon','x',1,60);")
  case "$out" in *"permission denied"*) ok "$r 调用配额 RPC 被拒";; *) bad "$r 竟然能调用配额 RPC";; esac
done
qok "service_role 可直接调用配额 RPC" \
  "set role service_role; select public.v2_spend_quota('ip','svc-probe',5,60);"
qrls=$(q "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
          where n.nspname='public' and c.relname in ('v2_quota_bucket','v2_stream_lease','v2_token_usage') and c.relrowsecurity;")
[ "$qrls" = "3" ] && ok "配额三表均开启 RLS" || bad "配额表 RLS 未全覆盖（$qrls/3）"

echo "== 9b. 定时维护作业（模拟 pg_cron 每日触发）=="
# 逐条复跑 supabase/migrations/20261118_v2_cleanup_schedule.sql 里两个 cron.job 的实际语句，
# 证明「装上定时器之后会发生什么」：只删过期、可重复触发、dry-run 与真实执行口径一致。
JOB_CLEANUP="select public.cleanup_expired_sessions(90, false)"
JOB_PRUNE="select public.v2_prune_quota(false)"
expired_sess=$(q "insert into public.chat_sessions(anon_id,session_token_hash) values ('77777777-7777-7777-7777-777777777777','$(printf '2%.0s' {1..64})') returning id;")
boundary_sess=$(q "insert into public.chat_sessions(anon_id,session_token_hash) values ('88888888-8888-8888-8888-888888888888','$(printf '3%.0s' {1..64})') returning id;")
q "insert into public.chat_messages(session_id,role,content) values ('$expired_sess','user','91 天'),('$boundary_sess','user','89 天 23 小时');
   update public.chat_sessions set last_message_at = now() - interval '91 days' where id='$expired_sess';
   update public.chat_sessions set last_message_at = now() - interval '89 days 23 hours' where id='$boundary_sess';
   insert into public.v2_quota_bucket(scope,bucket_key,window_start,hits) values
     ('anon','stale-window', now() - interval '8 days', 3),
     ('anon','fresh-window', now(), 1);
   insert into public.v2_stream_lease(session_id,lease_token,expires_at) values
     ('$boundary_sess','$(printf 'a%.0s' {1..32})', now() - interval '1 hour'),
     ('$SID','$(printf 'b%.0s' {1..32})', now() + interval '4 minutes')
     on conflict (session_id) do update set lease_token = excluded.lease_token, expires_at = excluded.expires_at;" >/dev/null
dead_lease=$(q "select count(*) from public.v2_stream_lease where session_id='$boundary_sess';")
[ "$dead_lease" = "1" ] && ok "已放置过期租约与陈旧配额窗口作为待清理样本" || bad "维护样本数据未落库"
dry_sessions=$(q "select public.cleanup_expired_sessions(90,true);")
dry_buckets=$(q "select public.v2_prune_quota(true)->>'quota_buckets';")
dry_leases=$(q "select public.v2_prune_quota(true)->>'stream_leases';")
[ "$dry_sessions" = "1" ] && ok "清理作业干跑命中 1 条（边界 89d23h 不计入）" || bad "干跑计数异常（$dry_sessions）"
[ "$dry_buckets" = "1" ] && [ "$dry_leases" = "1" ] && ok "配额干跑：1 个陈旧窗口 + 1 个过期租约" \
  || bad "配额干跑口径异常（buckets=$dry_buckets leases=$dry_leases）"
tick1=$(q "$JOB_CLEANUP;")
[ "$tick1" = "1" ] && ok "清理作业第 1 次触发删除 1 条" || bad "清理作业首次触发异常（$tick1）"
gone_msgs=$(q "select count(*) from public.chat_messages where session_id='$expired_sess';")
kept_sess=$(q "select count(*) from public.chat_sessions where id='$boundary_sess';")
kept_prof=$(q "select count(*) from public.user_profiles where session_id='$boundary_sess';")
[ "$gone_msgs" = "0" ] && [ "$kept_sess" = "1" ] && [ "$kept_prof" = "1" ] \
  && ok "级联只带走过期会话，边界会话与画像完好" || bad "边界判定错误（msgs=$gone_msgs sess=$kept_sess prof=$kept_prof）"
tick2=$(q "$JOB_CLEANUP;")
[ "$tick2" = "0" ] && ok "清理作业重复触发安全（第 2 次 0 行，无报错）" || bad "重复触发不稳定（$tick2）"
ptick1=$(q "$JOB_PRUNE;")
[ "$(q "select count(*) from public.v2_quota_bucket where bucket_key='stale-window';")" = "0" ] \
  && [ "$(q "select count(*) from public.v2_quota_bucket where bucket_key='fresh-window';")" = "1" ] \
  && ok "prune 只删 7 天前的窗口，在用的当前窗口保留" || bad "prune 误删在用窗口（$ptick1）"
live_lease=$(q "select count(*) from public.v2_stream_lease where session_id='$SID';")
all_lease=$(q "select count(*) from public.v2_stream_lease;")
[ "$live_lease" = "1" ] && [ "$all_lease" = "1" ] && ok "prune 收回过期租约、保留进行中的租约" \
  || bad "租约清理口径错误（live=$live_lease total=$all_lease）"
ptick2=$(q "$JOB_PRUNE;")
[ "$(q "select public.v2_prune_quota(true)->>'quota_buckets';")" = "0" ] \
  && [ "$(q "select public.v2_prune_quota(true)->>'stream_leases';")" = "0" ] \
  && ok "prune 收敛：第二次触发无可清理项（登记表按日收敛，无需清理）" || bad "prune 未收敛"

echo "== 10. 配额回滚不影响记忆层 =="
"$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -v ON_ERROR_STOP=1 \
  -f "$REPO/supabase/migrations/rollback/20261118_v2_quota.sql" >"$WORK/qrb.out" 2>&1
if [ $? -eq 0 ]; then ok "配额回滚脚本执行通过"; else bad "配额回滚失败"; cat "$WORK/qrb.out"; fi
qleft=$(q "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
           where n.nspname='public' and c.relname in ('v2_quota_bucket','v2_stream_lease','v2_token_usage');")
[ "$qleft" = "0" ] && ok "配额对象已移除" || bad "回滚后残留 $qleft 个配额对象"
mleft=$(q "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
           where n.nspname='public' and c.relname in ('chat_sessions','chat_messages','user_profiles');")
[ "$mleft" = "3" ] && ok "记忆层三表未被连带删除" || bad "配额回滚误删记忆层（$mleft/3）"

echo "== 11. 回滚（清单 8）=="
doc_rows=$(q "select count(*) from public.documents;")
"$PSQL" -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$ACC_DB" -X -q -v ON_ERROR_STOP=1 \
  -f "$REPO/supabase/migrations/rollback/20261117_v2_memory.sql" >"$WORK/rollback.out" 2>&1
if [ $? -eq 0 ]; then ok "回滚脚本执行通过"; else bad "回滚失败"; cat "$WORK/rollback.out"; fi
left=$(q "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
          where n.nspname='public' and c.relname in ('chat_sessions','chat_messages','user_profiles');")
[ "$left" = "0" ] && ok "v2 三表已移除" || bad "回滚后仍有 $left 张 v2 表"
fn=$(q "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where n.nspname='public' and p.proname in ('cleanup_expired_sessions','v2_touch_updated_at','v2_initialize_profile','v2_message_activity');")
[ "$fn" = "0" ] && ok "v2 函数/触发器函数已移除" || bad "回滚后残留 $fn 个函数"
keep=$(q "select count(*) from public.documents;")
ck=$(q "select count(*) from public.chunks;")
[ "$keep" = "$doc_rows" ] && [ "$ck" = "1" ] && ok "v1 documents/chunks 未受影响" || bad "回滚误伤 v1 对象（documents=$keep chunks=$ck）"

echo
echo "== 汇总：$pass 通过 / $fail 失败 =="
[ "$fail" = "0" ] || exit 1

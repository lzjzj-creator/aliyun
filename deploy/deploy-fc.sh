#!/usr/bin/env bash
# 部署两个后端到阿里云函数计算 FC3（Serverless Devs）
# 前置：npm i -g @serverless-devs/s && s config add（阿里云 AccessKey）
# 用法：bash deploy/deploy-fc.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
set -a; source "$ROOT/.env"; set +a

# 部署期暂放 CORS，Sites 域名确定后再收紧
export ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-*}"
export QWEN_MODEL_CHAT="${QWEN_MODEL_CHAT:-qwen-plus}"
export EMBEDDING_MODEL="${EMBEDDING_MODEL:-text-embedding-v4}"
export MIN_RETRIEVE_SCORE="${MIN_RETRIEVE_SCORE:-0.25}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
# 线上来源链接指向 GitHub Pages 公开镜像；本地 .env 的 127.0.0.1 只用于开发，不能带上 FC
export KB_SOURCE_BASE="${KB_MIRROR_URL:-https://lzjzj-creator.github.io}"

if [ -z "${ADMIN_PASSWORD_HASH:-}" ] || [ -z "${ADMIN_SESSION_SECRET:-}" ]; then
  echo '缺少管理员配置：先运行 python ingest/make_admin_password.py' >&2
  exit 1
fi
export ADMIN_SESSION_TTL="${ADMIN_SESSION_TTL:-7200}"

# v2 开关：默认全 0（不挂载 v2 路由）。开启顺序见 docs/部署清单.md：
# 先执行 supabase/migrations 下两个迁移 → 冒烟 → 再置 1。
# V2_SHARED_QUOTA=1 依赖 20261118_v2_quota.sql，否则 anon 轮次配额会 503（fail-closed）。
export V2_MEMORY_ENABLED="${V2_MEMORY_ENABLED:-0}"
export COACH_ENABLED="${COACH_ENABLED:-0}"
export V2_SHARED_QUOTA="${V2_SHARED_QUOTA:-0}"
export V2_IP_LIMIT_PER_MIN="${V2_IP_LIMIT_PER_MIN:-20}"
export V2_ANON_TURNS_PER_DAY="${V2_ANON_TURNS_PER_DAY:-100}"
export QWEN_MODEL_ROUTER="${QWEN_MODEL_ROUTER:-qwen-turbo}"
export COACH_MAX_TURNS="${COACH_MAX_TURNS:-12}"
export COACH_WRAP_COVERAGE="${COACH_WRAP_COVERAGE:-0.8}"
export COACH_WRAP_STAR="${COACH_WRAP_STAR:-0.6}"
export COACH_THINKING="${COACH_THINKING:-0}"
export COACH_DAILY_TOKEN_BUDGET="${COACH_DAILY_TOKEN_BUDGET:-200000}"
echo "v2 开关：memory=$V2_MEMORY_ENABLED coach=$COACH_ENABLED shared_quota=$V2_SHARED_QUOTA token_budget=$COACH_DAILY_TOKEN_BUDGET"

echo '=== 部署 zhimai-api (FastAPI) ==='
(cd "$ROOT/deploy/fc/zhimai-api" && s deploy -y)

echo '=== 部署 resume-analyze (Node) ==='
(cd "$ROOT/deploy/fc/resume-analyze" && s deploy -y)

echo '=== 完成，见上方 fcapp.run 域名 ==='

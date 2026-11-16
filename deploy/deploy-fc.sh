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
# 线上来源链接指向 CloudBase 公开镜像；本地 .env 的 127.0.0.1 只用于开发，不能带上 FC
export KB_SOURCE_BASE="${CLOUDBASE_KB_URL:-https://spicy-guanaco-d0gd9oj994ceedce6-1422760240.tcloudbaseapp.com}"

echo '=== 部署 zhimai-api (FastAPI) ==='
(cd "$ROOT/deploy/fc/zhimai-api" && s deploy -y)

echo '=== 部署 resume-analyze (Node) ==='
(cd "$ROOT/deploy/fc/resume-analyze" && s deploy -y)

echo '=== 完成，见上方 fcapp.run 域名 ==='

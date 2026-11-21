#!/usr/bin/env bash
# 部署两个后端到 CloudBase 云托管（云端构建，无需本地 Docker）
# 用法：bash deploy/deploy-cloudrun.sh
# 密钥从 ../.env 现场读取，不落任何文件/日志
set -euo pipefail

ENV_ID="${CLOUDBASE_ENV_ID:-spicy-guanaco-d0gd9oj994ceedce6}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

set -a; source "$ROOT/.env"; set +a

# 部署期暂时放开 CORS（Sites 域名确定后再收紧）
export ALLOWED_ORIGINS="*"

ENV_JSON=$(python -c "
import json, os
keys = ['SUPABASE_URL','SUPABASE_SERVICE_KEY','DASHSCOPE_API_KEY','DEEPSEEK_API_KEY',
        'ALLOWED_ORIGINS','QWEN_MODEL_CHAT','QWEN_MODEL_ROUTER','EMBEDDING_MODEL',
        'MIN_RETRIEVE_SCORE','V2_MEMORY_ENABLED','V3_MEMORY_ENABLED']
print(json.dumps({k: os.environ[k] for k in keys if os.environ.get(k)}))")

echo '=== 部署 zhimai-api (FastAPI) ==='
tcb cloudrun deploy --env-id "$ENV_ID" --service-name zhimai-api \
  --source "$ROOT/backend" --port 80 --wait --force \
  --open-access-types PUBLIC \
  --env-params "$ENV_JSON"

echo '=== 部署 resume-analyze (Node 简历诊断) ==='
ENV_JSON2=$(python -c "
import json, os
keys = ['DEEPSEEK_API_KEY','ALLOWED_ORIGINS']
print(json.dumps({k: os.environ[k] for k in keys if os.environ.get(k)}))")

tcb cloudrun deploy --env-id "$ENV_ID" --service-name resume-analyze \
  --source "$ROOT/vendor-resume-audit/api" --port 3000 --wait --force \
  --open-access-types PUBLIC \
  --env-params "$ENV_JSON2"

echo '=== 服务列表 ==='
tcb cloudrun list --env-id "$ENV_ID"

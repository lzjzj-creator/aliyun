#!/usr/bin/env bash
# 把知识库镜像发布到 CloudBase 静态网站托管（默认首页 index.html 已开启）
# 用法：bash deploy/deploy-mirror.sh
# 全量一次性上传会卡在 CLI 的准备阶段，所以按顶层目录分块；1002 专业规划再按二级目录拆分
# 注意：不要用 --prune，静态托管里还有平台 __auth/cloud-admin 文件
set -euo pipefail

ENV_ID="${CLOUDBASE_ENV_ID:-spicy-guanaco-d0gd9oj994ceedce6}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/mirror/dist"

upload() {
  echo "=== $2 ($(find "$1" -type f | wc -l) files) ==="
  tcb hosting deploy "$1" "$2" --env-id "$ENV_ID" --retry-count 3 --concurrency 8
}

for d in "$DIST"/*/; do
  name="$(basename "$d")"
  if [ "$name" = "1002中国大学的专业规划" ]; then
    for sub in "$d"*/; do
      upload "$sub" "$name/$(basename "$sub")"
    done
  else
    upload "$d" "$name"
  fi
done

# 根目录散文件（CSS/JS/index.html）
find "$DIST" -maxdepth 1 -type f | while read -r f; do
  upload "$f" "$(basename "$f")"
done

echo '=== 全部完成 ==='
tcb hosting detail --env-id "$ENV_ID"

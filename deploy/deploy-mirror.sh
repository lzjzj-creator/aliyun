#!/usr/bin/env bash
# 发布知识库镜像到 GitHub Pages（mirror/dist 是公开仓库 lzjzj-creator.github.io 的工作区）
# 用法：bash deploy/deploy-mirror.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/mirror/dist"

if [ ! -d "$DIST/.git" ]; then
  echo "mirror/dist 未初始化为 GitHub Pages 仓库；先克隆 lzjzj-creator.github.io 到该目录" >&2
  exit 1
fi

cd "$DIST"
touch .nojekyll
git add -A
if git diff --cached --quiet; then
  echo '=== 镜像无变化，跳过提交 ==='
else
  git commit -m "mirror: sync knowledge base $(date +%F)"
fi
git push origin main

echo '=== 已推送，等待 GitHub Pages 构建 ==='
echo 'https://lzjzj-creator.github.io/'

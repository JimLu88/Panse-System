#!/usr/bin/env bash
# 群晖唯一正式发布入口：同一个 origin/main 提交依次构建/部署 API 与 Web，再做只读验收。
# 用法（Git Bash）：bash scripts/deploy_release_nas.sh
set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/panse_nas}"
SSH_HOST="${SSH_HOST:-15068803006@DS923plus}"
SSH_PORT="${SSH_PORT:-2222}"
NAS_DOCKER="${NAS_DOCKER:-sudo /usr/local/bin/docker}"
NAS_DIR="${NAS_DIR:-/volume1/docker/panse}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -p "$SSH_PORT" "$SSH_HOST")

source scripts/lib/nas_deploy_guard.sh
panse_acquire_nas_deploy_lock
trap panse_release_nas_deploy_lock EXIT

git fetch origin main --quiet
head_full=$(git rev-parse HEAD)
origin_full=$(git rev-parse origin/main)
[[ "$head_full" == "$origin_full" ]] || {
  echo "FATAL: HEAD != origin/main。正式发布只允许已推送的 main 精确提交。" >&2
  exit 1
}
git diff --quiet && git diff --cached --quiet || {
  echo "FATAL: 工作区有未提交的已跟踪修改。" >&2
  exit 1
}

echo "=== Panse ERP unified release @ ${head_full:0:7} ==="
BUILD=1 bash scripts/deploy_api_nas.sh
bash scripts/deploy_web_nas.sh
EXPECTED_COMMIT="$head_full" bash scripts/verify_release_nas.sh
echo "DONE: API + Web 已从同一提交 ${head_full:0:7} 发布并验收"

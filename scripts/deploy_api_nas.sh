#!/usr/bin/env bash
# 把 panse-system-api 镜像部署到群晖 NAS。
#   流程: (可选 build) → docker save|gzip|ssh load → up -d --no-build api → 重启 web → 验证健康。
#   红线: 重建 api 后【必须】重启 web, 否则 lan nginx 缓存旧 api 容器 IP → /api 502。
#
# 用法 (在 PC 的 Git Bash 里跑, 不要用 PowerShell —— PowerShell 管道会损坏二进制镜像流):
#   先确保已 build 好镜像:
#     docker build ./backend -f backend/Dockerfile -t panse-system-api:latest \
#       --build-arg GIT_COMMIT=$(git rev-parse --short HEAD)
#   再部署:
#     bash scripts/deploy_api_nas.sh
#   或一步到位 (脚本内 build):
#     BUILD=1 bash scripts/deploy_api_nas.sh
set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/panse_nas}"
SSH_HOST="${SSH_HOST:-15068803006@DS923plus}"
SSH_PORT="${SSH_PORT:-2222}"
NAS_DOCKER="${NAS_DOCKER:-sudo /usr/local/bin/docker}"
NAS_DIR="${NAS_DIR:-/volume1/docker/panse}"
IMAGE="${IMAGE:-panse-system-api:latest}"
WEB_HEALTH_URL="${WEB_HEALTH_URL:-http://192.168.31.21:8200/api/health}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -p "$SSH_PORT" "$SSH_HOST")

if [[ "${BUILD:-0}" == "1" ]]; then
  gc=$(git rev-parse --short HEAD)
  echo "[build] panse-system-api:latest @ $gc"
  docker build ./backend -f backend/Dockerfile -t "$IMAGE" \
    --build-arg GIT_COMMIT="$gc" \
    --build-arg GIT_COMMIT_MSG="$(git log -1 --pretty=%s)" \
    --build-arg GIT_COMMIT_DATE="$(git log -1 --date=short --pretty=%cd)"
fi

echo "[1/5] 预检 SSH + 当前 panse-system 容器"
"${SSH[@]}" "$NAS_DOCKER ps --filter name=panse-system --format '{{.Names}}  {{.Status}}'"

echo "[2/5] 传镜像 (docker save | gzip | ssh -> gunzip | docker load)"
docker save "$IMAGE" | gzip | "${SSH[@]}" "gunzip | $NAS_DOCKER load"

echo "[3/5] 起新 api 容器 (用载入的镜像, 不在 NAS 上 build)"
"${SSH[@]}" "cd $NAS_DIR && $NAS_DOCKER compose -p panse-system up -d --no-build api"

echo "[4/5] 重启 web (nginx 重新解析 api 容器 IP, 防 502) —— 红线步骤"
"${SSH[@]}" "$NAS_DOCKER restart panse-system-web-1"

echo "[5/5] 验证健康 (api 容器内 + web 反代)"
sleep 5
api_h=$("${SSH[@]}" "$NAS_DOCKER exec panse-system-api-1 wget -qO- http://localhost:8000/api/health" || true)
echo "  api 内部 /api/health = ${api_h:-<空>}"
web_h=$(curl -fsS --max-time 15 "$WEB_HEALTH_URL" || true)
echo "  web 反代 $WEB_HEALTH_URL = ${web_h:-<空>}"
if [[ "$api_h" == *'"ok":true'* && "$web_h" == *'"ok":true'* ]]; then
  echo "DONE: api + web 均健康"
else
  echo "WARN: 健康检查未全绿, 请人工确认 (api 容器是否 running / web 是否需再 restart)" >&2
  exit 1
fi

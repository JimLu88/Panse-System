#!/usr/bin/env bash
# 把 panse-system-web (nginx + React 构建产物) 部署到群晖 NAS。
#   流程: build(复用层缓存, 前端没改则秒过) → 回收构建缓存 → save|gzip|ssh load → up -d web(-f lan)。
#   红线1: 重建 web 必须带 -f docker-compose.lan.yml, 否则撞 DSM 443。
#   红线2: 前端源码必须已和另一台机统一(git 无分叉), 否则会回退别人的前端。
#
# 用法 (在 PC 的 Git Bash 里跑, 不要用 PowerShell —— 管道会损坏二进制镜像流):
#   bash scripts/deploy_web_nas.sh
set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/panse_nas}"
SSH_HOST="${SSH_HOST:-15068803006@DS923plus}"
SSH_PORT="${SSH_PORT:-2222}"
NAS_DOCKER="${NAS_DOCKER:-sudo /usr/local/bin/docker}"
NAS_DIR="${NAS_DIR:-/volume1/docker/panse}"
IMAGE="${IMAGE:-panse-system-web:lan}"
WEB_HEALTH_URL="${WEB_HEALTH_URL:-http://192.168.31.21:8200/api/health}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -p "$SSH_PORT" "$SSH_HOST")

echo "[1/5] build web.lan (Docker 层缓存: 前端没改则秒过, 不会全量重建)"
BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker build -f deploy/web.lan.Dockerfile -t "$IMAGE" .

echo "[2/5] 回收: 悬空镜像 + 构建缓存压到 ≤6GB (防 vhdx 疯长吃满 C 盘)"
docker image prune -f >/dev/null 2>&1 || true
docker builder prune -f --keep-storage=6GB >/dev/null 2>&1 || true

echo "[3/5] 传镜像 (docker save | gzip | ssh -> gunzip | docker load)"
docker save "$IMAGE" | gzip | "${SSH[@]}" "gunzip | $NAS_DOCKER load"

echo "[4/5] 重建 web 容器 (红线: 必须 -f docker-compose.lan.yml 防撞 443)"
"${SSH[@]}" "cd $NAS_DIR && $NAS_DOCKER compose -p panse-system -f docker-compose.lan.yml up -d web"

echo "[5/5] 验证健康"
sleep 5
web_h=$(curl -fsS --max-time 15 "$WEB_HEALTH_URL" || true)
echo "  web 反代 $WEB_HEALTH_URL = ${web_h:-<空>}"
if [[ "$web_h" == *'"ok":true'* ]]; then
  echo "DONE: web 已部署且健康"
else
  echo "WARN: 健康检查未绿, 请人工确认 (web 容器是否 running / api 是否在线)" >&2
  exit 1
fi

#!/usr/bin/env bash
# 把 panse-system-web (nginx + React 构建产物) 部署到群晖 NAS。
#   流程: 同源预检 → build+版本标记 → 备份旧镜像 → save|gzip|ssh load → up -d web(-f lan) → 版本验证。
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
WEB_BASE_URL="${WEB_BASE_URL:-http://192.168.31.21:8200}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -p "$SSH_PORT" "$SSH_HOST")

source scripts/lib/nas_deploy_guard.sh
if [[ "${PANSE_NAS_DEPLOY_LOCK_HELD:-0}" != "1" ]]; then
  panse_acquire_nas_deploy_lock
  trap panse_release_nas_deploy_lock EXIT
fi

if [[ "${FORCE:-0}" != "1" ]]; then
  git fetch origin main --quiet
  head_full=$(git rev-parse HEAD)
  origin_full=$(git rev-parse origin/main)
  if [[ "$head_full" != "$origin_full" ]]; then
    echo "FATAL: HEAD($head_full) != origin/main($origin_full)。只允许从已推送的 main 精确提交部署; 紧急跳过用 FORCE=1。" >&2
    exit 1
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "FATAL: 工作区有未提交的已跟踪修改。先提交并推送，再部署。" >&2
    exit 1
  fi
fi

head_full=$(git rev-parse HEAD)
head_short=$(git rev-parse --short HEAD)
commit_date=$(git log -1 --date=short --pretty=%cd)
build_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "[1/7] build web.lan @ $head_short (Docker 层缓存: 前端没改则秒过)"
BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker build -f deploy/web.lan.Dockerfile -t "$IMAGE" \
  --build-arg GIT_COMMIT="$head_full" \
  --build-arg GIT_COMMIT_DATE="$commit_date" \
  --build-arg BUILD_TIME="$build_time" .

img_rev=$(docker image inspect "$IMAGE" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')
if [[ "$img_rev" != "$head_full" ]]; then
  echo "FATAL: Web 镜像版本标记($img_rev) != HEAD($head_full)。" >&2
  exit 1
fi
echo "  镜像版本标记一致 ✓ ($head_short)"

echo "[2/7] 回收: 悬空镜像 + 构建缓存压到 ≤6GB (防 vhdx 疯长吃满 C 盘)"
docker image prune -f >/dev/null 2>&1 || true
docker builder prune -f --keep-storage=6GB >/dev/null 2>&1 || true

rollback="panse-system-web:rollback-$(date +%Y%m%d-%H%M%S)"
echo "[3/7] 给 NAS 当前 Web 镜像打回滚标签: $rollback"
"${SSH[@]}" "$NAS_DOCKER image inspect '$IMAGE' >/dev/null 2>&1 && $NAS_DOCKER tag '$IMAGE' '$rollback' || true"

echo "[4/7] 传镜像 (docker save | gzip | ssh -> gunzip | docker load)"
docker save "$IMAGE" | gzip | "${SSH[@]}" "gunzip | $NAS_DOCKER load"

echo "[5/7] 重建 web 容器 (红线: 必须 -f docker-compose.lan.yml 防撞 443)"
"${SSH[@]}" "cd $NAS_DIR && $NAS_DOCKER compose -p panse-system -f docker-compose.lan.yml up -d web"

echo "[6/7] 验证健康"
sleep 5
web_h=$(curl -fsS --max-time 15 "$WEB_HEALTH_URL" || true)
echo "  web 反代 $WEB_HEALTH_URL = ${web_h:-<空>}"
if [[ "$web_h" != *'"ok":true'* ]]; then
  echo "WARN: 健康检查未绿, 请人工确认 (web 容器是否 running / api 是否在线)" >&2
  exit 1
fi

echo "[7/7] 验证线上 Web 构建提交"
web_v=$(curl -fsS --max-time 15 "$WEB_BASE_URL/build-version.json" || true)
echo "  build-version.json = ${web_v:-<空>}"
if [[ "$web_v" != *"$head_full"* ]]; then
  echo "FATAL: 线上 Web 不是本次提交 $head_short。可用回滚标签 $rollback 恢复。" >&2
  exit 1
fi
echo "DONE: web @ $head_short 已部署且健康；回滚镜像=$rollback"

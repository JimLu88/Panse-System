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

# 落后 origin 拦截 (2026-07-12 事故: 本地分支 behind 30 未察觉, origin 的迁移0125已上库,
# 旧代码镜像一部署 alembic 找不到 revision → api 重启循环宕机 7 分钟。FORCE=1 可跳过)。
if [[ "${FORCE:-0}" != "1" ]]; then
  git fetch origin main --quiet 2>/dev/null || true
  behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
  if [[ "${behind:-0}" -gt 0 ]]; then
    echo "FATAL: 本地分支落后 origin/main $behind 个提交 (可能含新迁移)。先 rebase 再部署; 明知故犯用 FORCE=1。" >&2
    exit 1
  fi
fi

if [[ "${BUILD:-0}" == "1" ]]; then
  gc=$(git rev-parse --short HEAD)
  echo "[build] panse-system-api:latest @ $gc"
  docker build ./backend -f backend/Dockerfile -t "$IMAGE" \
    --build-arg GIT_COMMIT="$gc" \
    --build-arg GIT_COMMIT_MSG="$(git log -1 --pretty=%s)" \
    --build-arg GIT_COMMIT_DATE="$(git log -1 --date=short --pretty=%cd)"
  # 镜像内容指纹自检 (2026-07-12 事故: Docker Desktop 崩溃后 BuildKit 增量上下文吐旧文件,
  # COPY 进镜像的是 7/2-7/4 的代码而构建参数是新提交号 —— 元数据会骗人, 只有内容不会)。
  echo "[build] 内容指纹自检 (本地 backend/app+alembic vs 镜像内)…"
  loc_md5=$( (find backend/app backend/alembic -name '*.py' -type f | LC_ALL=C sort | xargs cat) | md5sum | cut -d' ' -f1)
  img_md5=$(docker run --rm --entrypoint sh "$IMAGE" -c \
    "(find /app/app /app/alembic -name '*.py' -type f | LC_ALL=C sort | xargs cat) | md5sum" | cut -d' ' -f1)
  if [[ "$loc_md5" != "$img_md5" ]]; then
    echo "FATAL: 镜像内容 ≠ 工作区 (BuildKit 缓存吐旧文件?)。跑 docker builder prune -af 后重试。" >&2
    echo "  local=$loc_md5 image=$img_md5" >&2
    exit 1
  fi
  echo "[build] 指纹一致 ✓ ($loc_md5)"
  # 构建后自动回收 (2026-07-04): 悬空镜像 + 构建缓存压到 ≤6GB → 防 Docker vhdx 疯长吃满 C 盘。
  # 保留 6GB 缓存让下次 build 仍走增量 (只重传改动层), 不至于全量重建。
  echo "[build] 回收悬空镜像 + 压缩构建缓存(保留6GB)…"
  docker image prune -f >/dev/null 2>&1 || true
  docker builder prune -f --keep-storage=6GB >/dev/null 2>&1 || true
fi

# 不带 BUILD=1 也要指纹自检 (2026-07-12 二次事故: 忘了 BUILD=1, 把上个会话的旧镜像原样推上生产,
# 健康检查全绿但代码是旧的 —— 自检原先只在 build 分支里, 无 build 部署完全裸奔)。
if [[ "${BUILD:-0}" != "1" ]]; then
  echo "[check] 无 build 部署: 内容指纹自检 (本地 backend/app+alembic vs 待推镜像)…"
  loc_md5=$( (find backend/app backend/alembic -name '*.py' -type f | LC_ALL=C sort | xargs cat) | md5sum | cut -d' ' -f1)
  img_md5=$(docker run --rm --entrypoint sh "$IMAGE" -c \
    "(find /app/app /app/alembic -name '*.py' -type f | LC_ALL=C sort | xargs cat) | md5sum" | cut -d' ' -f1)
  if [[ "$loc_md5" != "$img_md5" ]]; then
    echo "FATAL: 本地镜像内容 ≠ 当前工作区 —— 这不是本次代码构建的镜像。用 BUILD=1 重新构建后部署。" >&2
    echo "  local=$loc_md5 image=$img_md5" >&2
    exit 1
  fi
  echo "[check] 指纹一致 ✓ ($loc_md5)"
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

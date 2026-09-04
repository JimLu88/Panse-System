#!/usr/bin/env bash
# 把 panse-system-api 镜像部署到群晖 NAS。
#   流程: 同源预检 → (可选 build) → 备份旧镜像 → docker save|gzip|ssh load → up api → 重启 web → 版本验证。
#   红线: 重建 api 后【必须】重启 web, 否则 lan nginx 缓存旧 api 容器 IP → /api 502。
#
# 用法 (在 PC 的 Git Bash 里跑, 不要用 PowerShell —— PowerShell 管道会损坏二进制镜像流):
#   先确保已 build 好镜像:
#     docker build . -f backend/Dockerfile -t panse-system-api:latest \
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

source scripts/lib/nas_deploy_guard.sh
if [[ "${PANSE_NAS_DEPLOY_LOCK_HELD:-0}" != "1" ]]; then
  panse_acquire_nas_deploy_lock
  trap panse_release_nas_deploy_lock EXIT
fi

# 只允许部署 origin/main 的精确提交。既拦截落后，也拦截“本地已提交但未推送”的版本，
# 防 NAS 运行一个 GitHub 上无法重建/追溯的镜像。FORCE=1 仅供明确的紧急恢复。
if [[ "${FORCE:-0}" != "1" ]]; then
  git fetch origin main --quiet
  head_full=$(git rev-parse HEAD)
  origin_full=$(git rev-parse origin/main)
  if [[ "$head_full" != "$origin_full" ]]; then
    echo "FATAL: HEAD($head_full) != origin/main($origin_full)。先同步并推送 main; 紧急跳过用 FORCE=1。" >&2
    exit 1
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "FATAL: 工作区有未提交的已跟踪修改。先提交并推送，再部署。" >&2
    exit 1
  fi
fi

# Never deploy code that is older than the schema already present in
# production. This is checked while holding the NAS-wide deployment lock, so
# another workstation cannot advance the DB between this gate and the switch.
panse_require_candidate_supports_database_revision

if [[ "${BUILD:-0}" == "1" ]]; then
  gc=$(git rev-parse HEAD)
  gc_short=$(git rev-parse --short HEAD)
  echo "[build] panse-system-api:latest @ $gc_short"
  docker build . -f backend/Dockerfile -t "$IMAGE" \
    --load \
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
  loc_policy_sha=$(sha256sum TAOBAO_CAMPAIGN_SIGNUP_POLICY.json | cut -d' ' -f1)
  img_policy_sha=$(MSYS_NO_PATHCONV=1 docker run --rm --entrypoint sha256sum "$IMAGE" \
    /app/TAOBAO_CAMPAIGN_SIGNUP_POLICY.json | cut -d' ' -f1)
  if [[ "$loc_policy_sha" != "$img_policy_sha" ]]; then
    echo "FATAL: 镜像活动报名规则 ≠ 仓库根目录规则，拒绝部署。" >&2
    echo "  local=$loc_policy_sha image=$img_policy_sha" >&2
    exit 1
  fi
  echo "[build] 活动报名规则指纹一致 ✓ ($loc_policy_sha)"
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
  loc_policy_sha=$(sha256sum TAOBAO_CAMPAIGN_SIGNUP_POLICY.json | cut -d' ' -f1)
  img_policy_sha=$(MSYS_NO_PATHCONV=1 docker run --rm --entrypoint sha256sum "$IMAGE" \
    /app/TAOBAO_CAMPAIGN_SIGNUP_POLICY.json | cut -d' ' -f1)
  if [[ "$loc_policy_sha" != "$img_policy_sha" ]]; then
    echo "FATAL: 待推镜像活动报名规则 ≠ 仓库根目录规则，拒绝部署。" >&2
    echo "  local=$loc_policy_sha image=$img_policy_sha" >&2
    exit 1
  fi
  echo "[check] 活动报名规则指纹一致 ✓ ($loc_policy_sha)"
fi

expected_full=$(git rev-parse HEAD)
expected_short=$(git rev-parse --short HEAD)

echo "[1/7] 预检 SSH + 当前 panse-system 容器"
"${SSH[@]}" "$NAS_DOCKER ps --filter name=panse-system --format '{{.Names}}  {{.Status}}'"

rollback="panse-system-api:rollback-$(date +%Y%m%d-%H%M%S)"
echo "[2/7] 给 NAS 当前 API 镜像打回滚标签: $rollback"
"${SSH[@]}" "$NAS_DOCKER image inspect '$IMAGE' >/dev/null 2>&1 && $NAS_DOCKER tag '$IMAGE' '$rollback' || true"

echo "[3/7] 传镜像 (docker save | gzip | ssh -> gunzip | docker load)"
docker save "$IMAGE" | gzip | "${SSH[@]}" "gunzip | $NAS_DOCKER load"

echo "[4/7] 起新 api 容器 (用载入的镜像, 不在 NAS 上 build)"
"${SSH[@]}" "cd $NAS_DIR && $NAS_DOCKER compose -p panse-system up -d --no-build api"

echo "[5/7] 重启 web (nginx 重新解析 api 容器 IP, 防 502) —— 红线步骤"
"${SSH[@]}" "$NAS_DOCKER restart panse-system-web-1"

echo "[6/7] 验证健康 (api 容器内 + web 反代)"
sleep 5
api_h=$("${SSH[@]}" "$NAS_DOCKER exec panse-system-api-1 wget -qO- http://localhost:8000/api/health" || true)
echo "  api 内部 /api/health = ${api_h:-<空>}"
web_h=$(curl -fsS --max-time 15 "$WEB_HEALTH_URL" || true)
echo "  web 反代 $WEB_HEALTH_URL = ${web_h:-<空>}"
if [[ "$api_h" != *'"ok":true'* || "$web_h" != *'"ok":true'* ]]; then
  echo "WARN: 健康检查未全绿, 请人工确认 (api 容器是否 running / web 是否需再 restart)" >&2
  exit 1
fi

echo "[7/7] 验证线上 API 构建提交"
api_v=$(curl -fsS --max-time 15 "${WEB_HEALTH_URL%/api/health}/api/version" || true)
echo "  /api/version = ${api_v:-<空>}"
if [[ "$api_v" != *"$expected_full"* ]]; then
  echo "FATAL: 线上 API 不是本次提交 $expected_short。可用回滚标签 $rollback 恢复。" >&2
  exit 1
fi
echo "DONE: api @ $expected_short + web 反代均健康；回滚镜像=$rollback"

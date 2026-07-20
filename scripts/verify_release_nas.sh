#!/usr/bin/env bash
# 只读验收群晖生产发布：API/Web 同一提交、DB 迁移到 head、关键新路由与前端功能均存在。
set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/panse_nas}"
SSH_HOST="${SSH_HOST:-15068803006@DS923plus}"
SSH_PORT="${SSH_PORT:-2222}"
NAS_DOCKER="${NAS_DOCKER:-sudo /usr/local/bin/docker}"
BASE_URL="${BASE_URL:-http://192.168.31.21:8200}"
EXPECTED_COMMIT="${EXPECTED_COMMIT:-$(git rev-parse HEAD)}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -p "$SSH_PORT" "$SSH_HOST")

fail() { echo "FATAL: $*" >&2; exit 1; }
pass() { echo "  ✓ $*"; }

echo "[verify] expected=${EXPECTED_COMMIT:0:7} base=$BASE_URL"
health=$(curl -fsS --max-time 15 "$BASE_URL/api/health")
ready=$(curl -fsS --max-time 15 "$BASE_URL/api/ready")
[[ "$health" == *'"ok":true'* ]] || fail "API health 未通过: $health"
[[ "$ready" == *'"ready":true'* ]] || fail "API ready 未通过: $ready"
pass "health + ready"

api_v=$(curl -fsS --max-time 15 "$BASE_URL/api/version")
web_v=$(curl -fsS --max-time 15 "$BASE_URL/build-version.json")
[[ "$api_v" == *"$EXPECTED_COMMIT"* ]] || fail "API 版本不等于 expected: $api_v"
[[ "$web_v" == *"$EXPECTED_COMMIT"* ]] || fail "Web 版本不等于 expected: $web_v"
pass "API/Web 同一提交 ${EXPECTED_COMMIT:0:7}"

openapi=$(curl -fsS --max-time 30 "$BASE_URL/api/openapi.json")
[[ "$openapi" == *'"/api/campaigns"'* ]] || fail "OpenAPI 缺 /api/campaigns"
[[ "$openapi" == *'"/api/customization/v2/quote-both"'* ]] || fail "OpenAPI 缺定制报价双口径路由"
pass "活动生命周期 + 定制报价双口径后端路由"

index=$(curl -fsS --max-time 15 "$BASE_URL/")
js_path=$(printf '%s' "$index" | grep -oE '/assets/index-[^" ]+\.js' | head -1)
[[ -n "$js_path" ]] || fail "首页未找到 JS bundle"
bundle=$(curl -fsS --max-time 30 "$BASE_URL$js_path")
[[ "$bundle" == *'活动生命周期'* ]] || fail "Web bundle 缺活动生命周期界面"
[[ "$bundle" == *'按纯定制方向'* ]] || fail "Web bundle 缺纯定制双口径界面"
[[ "$bundle" == *'真实部件板单'* ]] || fail "Web bundle 缺真实 BOM 带入核对界面"
pass "三个目标前端功能均在在线 bundle"

migration=$("${SSH[@]}" "$NAS_DOCKER exec panse-system-api-1 alembic current" 2>&1)
[[ "$migration" == *'(head)'* ]] || fail "数据库迁移未到 head: $migration"
pass "数据库迁移到 head: $(printf '%s' "$migration" | tail -1)"

containers=$("${SSH[@]}" "$NAS_DOCKER ps --filter name=panse-system --format '{{.Names}}|{{.Status}}'")
printf '%s\n' "$containers"
for name in api web db backup; do
  [[ "$containers" == *"panse-system-$name-1|Up"* ]] || fail "$name 容器未运行"
done
pass "api/web/db/backup 容器均运行"
echo "PASS: 群晖发布验收完成"

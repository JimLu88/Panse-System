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

# LAN nginx 只代理 /api/*；FastAPI 的 /openapi.json 不从 8200 暴露，因此在 API 容器内只读获取。
openapi=$("${SSH[@]}" "$NAS_DOCKER exec panse-system-api-1 wget -qO- http://localhost:8000/openapi.json")
[[ "$openapi" == *'"/api/campaigns"'* ]] || fail "OpenAPI 缺 /api/campaigns"
[[ "$openapi" == *'"/api/customization/v2/quote-both"'* ]] || fail "OpenAPI 缺定制报价双口径路由"
[[ "$openapi" == *'"/api/procurement/tasks"'* ]] || fail "OpenAPI 缺采购任务路由"
[[ "$openapi" == *'"/api/procurement/agent/heartbeat"'* ]] || fail "OpenAPI 缺采购执行器心跳路由"
[[ "$openapi" == *'"/api/wechat/callback"'* ]] || fail "OpenAPI 缺企业微信入站回调路由"
[[ "$openapi" == *'"/api/wechat/aibot/callback"'* ]] || fail "OpenAPI 缺企业微信群聊智能机器人回调路由"
pass "既有关键路由 + 采购模块 + 企业微信入站回调路由"

# 页面采用 lazy chunk，目标文案不一定在首页主 bundle；直接检查当前在线 web 容器的全部静态 chunk。
web_features=$("${SSH[@]}" "$NAS_DOCKER exec panse-system-web-1 sh -c '
  grep -R -q "活动生命周期" /usr/share/nginx/html/assets && echo campaign=yes
  grep -R -q "按纯定制方向" /usr/share/nginx/html/assets && echo custom=yes
  grep -R -q "真实部件板单" /usr/share/nginx/html/assets && echo bom=yes
  grep -R -q "智能询价" /usr/share/nginx/html/assets && echo procurement=yes
  grep -R -q "你的确认稿" /usr/share/nginx/html/assets && echo procurement_review=yes
  grep -R -q "计算基准尺寸" /usr/share/nginx/html/assets && echo quote_basis_dimensions=yes
  grep -R -q "企业微信接收发货密码" /usr/share/nginx/html/assets && echo wechat_password_inbound=yes
  grep -R -q "群聊智能机器人" /usr/share/nginx/html/assets && echo wechat_aibot_inbound=yes
  true
'")
[[ "$web_features" == *'campaign=yes'* ]] || fail "Web 静态资源缺活动生命周期界面"
[[ "$web_features" == *'custom=yes'* ]] || fail "Web 静态资源缺纯定制双口径界面"
[[ "$web_features" == *'bom=yes'* ]] || fail "Web 静态资源缺真实 BOM 带入核对界面"
[[ "$web_features" == *'procurement=yes'* ]] || fail "Web 静态资源缺智能询价界面"
[[ "$web_features" == *'procurement_review=yes'* ]] || fail "Web 静态资源缺采购人工改稿门槛"
[[ "$web_features" == *'quote_basis_dimensions=yes'* ]] || fail "Web 静态资源缺报价计算基准尺寸"
[[ "$web_features" == *'wechat_password_inbound=yes'* ]] || fail "Web 静态资源缺企业微信密码接收配置"
[[ "$web_features" == *'wechat_aibot_inbound=yes'* ]] || fail "Web 静态资源缺企业微信群聊智能机器人配置"
pass "既有目标功能 + 企业微信密码接收配置均在在线 bundle"

migration=$("${SSH[@]}" "$NAS_DOCKER exec panse-system-api-1 alembic current" 2>&1)
[[ "$migration" == *'(head)'* ]] || fail "数据库迁移未到 head: $migration"
pass "数据库迁移到 head: $(printf '%s' "$migration" | tail -1)"

procurement_tables=$("${SSH[@]}" "$NAS_DOCKER exec panse-system-db-1 psql -U panse -d panse_erp -Atc \"select tablename from pg_tables where schemaname='public' and tablename like 'procurement_%' order by tablename\"")
for table in procurement_agent_states procurement_inquiries procurement_messages procurement_tasks; do
  [[ "$procurement_tables" == *"$table"* ]] || fail "数据库缺采购表 $table"
done
pass "四张采购表已独立创建"

containers=$("${SSH[@]}" "$NAS_DOCKER ps --filter name=panse-system --format '{{.Names}}|{{.Status}}'")
printf '%s\n' "$containers"
for name in api web db backup; do
  [[ "$containers" == *"panse-system-$name-1|Up"* ]] || fail "$name 容器未运行"
done
pass "api/web/db/backup 容器均运行"
echo "PASS: 群晖发布验收完成"

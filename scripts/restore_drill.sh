#!/usr/bin/env bash
# 畔色 ERP 备份恢复演练 (评审#11): 把最新备份回灌到一个临时库, 断言关键表有数据,
# 证明"备份不仅存在、而且能恢复"。建议 cron 每周跑一次:
#   0 4 * * 1  /volume1/docker/panse/scripts/restore_drill.sh >> /volume1/docker/panse/backups/drill.log 2>&1
# 群晖上 docker 用全路径: 设 DOCKER=/usr/local/bin/docker。
set -uo pipefail

CONTAINER="${POSTGRES_CONTAINER:-panse-system-db-1}"
PGUSER_="${POSTGRES_USER:-panse}"
BACKUP_DIR="${BACKUP_DIR:-/volume1/docker/panse/backups}"
DOCKER="${DOCKER:-docker}"
TMPDB="panse_drill_$(date +%s)"

latest=$(ls -t "$BACKUP_DIR"/panse-*.sql.gz "$BACKUP_DIR"/panse_*.sql.gz 2>/dev/null | head -1)
if [ -z "$latest" ]; then echo "[drill] ❌ 找不到备份文件 ($BACKUP_DIR)"; exit 1; fi
echo "[drill] $(date -Iseconds) 用最新备份: $latest → 临时库 $TMPDB"

$DOCKER exec "$CONTAINER" psql -U "$PGUSER_" -d postgres -c "CREATE DATABASE \"$TMPDB\"" || { echo "[drill] ❌ 建临时库失败"; exit 1; }
gunzip -c "$latest" | $DOCKER exec -i "$CONTAINER" psql -U "$PGUSER_" -d "$TMPDB" -q >/dev/null 2>&1
rows=$($DOCKER exec "$CONTAINER" psql -U "$PGUSER_" -d "$TMPDB" -tAc \
  "select (select count(*) from orders)+(select count(*) from alipay_flows)" 2>/dev/null || echo 0)
$DOCKER exec "$CONTAINER" psql -U "$PGUSER_" -d postgres -c "DROP DATABASE \"$TMPDB\"" >/dev/null 2>&1

if [ "${rows:-0}" -gt 0 ]; then
  echo "[drill] ✅ 恢复成功, orders+alipay 行数=$rows"
  exit 0
else
  echo "[drill] ❌ 恢复后关键表为空 — 备份可能损坏, 请人工检查!"
  exit 1
fi

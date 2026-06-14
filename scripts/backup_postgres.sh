#!/usr/bin/env bash
# 畔色 ERP 数据库定时备份 (plan §12.3 / Phase 6).
#
# 用法 (cron, 凌晨 3 点):
#   0 3 * * *  /opt/panse/scripts/backup_postgres.sh >> /var/log/panse-backup.log 2>&1
#
# 用 docker exec 调容器内的 pg_dump，输出到挂载目录，保留 30 天。

set -euo pipefail

CONTAINER="${POSTGRES_CONTAINER:-panse-system-db-1}"
USER="${POSTGRES_USER:-panse}"
DB="${POSTGRES_DB:-panse_erp}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/panse}"
KEEP_DAYS="${KEEP_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
ts=$(date +%Y%m%d_%H%M%S)
out="$BACKUP_DIR/panse_${ts}.sql.gz"

echo "[$(date -Iseconds)] start dump → $out"
docker exec -e PGUSER="$USER" "$CONTAINER" \
  pg_dump --no-owner --no-privileges "$DB" | gzip > "$out"   # 明文 SQL(与 compose 备份 / psql 恢复一致, 评审#11; 去掉 -Fc 自定义格式)

# 校验非空
if [[ ! -s "$out" ]]; then
  echo "ERROR: empty dump file" >&2
  rm -f "$out"
  exit 1
fi

echo "[$(date -Iseconds)] done ($(du -h "$out" | cut -f1))"

# 清理超过 KEEP_DAYS 的旧备份
find "$BACKUP_DIR" -name 'panse_*.sql.gz' -mtime "+$KEEP_DAYS" -delete -print

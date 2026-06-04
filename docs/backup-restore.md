# 备份与恢复演练

## 备份现状

- `backup` 容器每天 03:00 自动 `pg_dump | gzip` 到主机 `./backups/panse-YYYYMMDD-HHMMSS.sql.gz`,保留 30 天。
- 已加 `set -o pipefail` + 产物大小校验:`pg_dump` 失败不会再产出"看似成功"的损坏文件;**且只有本次备份成功才清理旧备份**,连续失败时旧的好备份不会被删。

## 手动立即备份

```bash
docker compose exec -T db sh -c 'pg_dump -U panse panse_erp | gzip' > backups/manual-$(date +%Y%m%d-%H%M%S).sql.gz
# 校验非空
ls -lh backups/manual-*.sql.gz | tail -1
```

## 恢复(restore)——务必先在测试环境演练

备份是明文 SQL(`pg_dump` 默认格式),用 `psql` 回灌。

### A. 恢复到一个**临时库**验证备份可用(推荐每月做一次演练)

```bash
# 1) 建临时库
docker compose exec -T db psql -U panse -c "CREATE DATABASE panse_restore_test;"
# 2) 回灌某个备份
gunzip -c backups/panse-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U panse -d panse_restore_test
# 3) 抽查关键表行数
docker compose exec -T db psql -U panse -d panse_restore_test -c "SELECT count(*) FROM orders; SELECT count(*) FROM pricing_sku;"
# 4) 验证完删掉临时库
docker compose exec -T db psql -U panse -c "DROP DATABASE panse_restore_test;"
```

### B. 真正灾难恢复(覆盖生产库)——**会清空现有数据,谨慎**

```bash
# 0) 先停 API, 避免写入
docker compose stop api
# 1) 删并重建库
docker compose exec -T db psql -U panse -c "DROP DATABASE panse_erp;"
docker compose exec -T db psql -U panse -c "CREATE DATABASE panse_erp;"
# 2) 回灌备份
gunzip -c backups/panse-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U panse -d panse_erp
# 3) 起 API (启动时自动 alembic upgrade head 补齐结构)
docker compose start api
```

## 演练检查清单(建议每月一次)

- [ ] 最近一份 `backups/panse-*.sql.gz` 大小 > 几百 KB(非 0、非异常小)
- [ ] 能成功恢复到临时库(流程 A)
- [ ] 临时库里 `orders` / `pricing_sku` / `alipay_flows` 行数与生产接近
- [ ] 演练完已 DROP 临时库

## 异地备份(强烈建议)

`backups/` 在 NAS 本机,**NAS 整机故障 / 被勒索 / 误删卷时本地备份会一起丢**。务必再同步一份到别处,三选一:

```bash
# A. Synology Hyper Backup (推荐): 套件中心装 Hyper Backup,
#    源选 Panse-System/backups 文件夹, 目标选 Synology C2 云 / 另一台 NAS / 外接硬盘, 设每日。

# B. rclone 同步到云盘 (阿里云盘/OneDrive/S3 等), 配合 cron 每日:
rclone sync /volume1/.../Panse-System/backups remote:panse-backup --max-age 35d

# C. rsync 到另一台机器:
rsync -az --delete /volume1/.../Panse-System/backups/ user@otherhost:/backup/panse/
```

> 验证异地副本同样要能恢复(走上面流程 A),"有备份"和"能恢复"是两回事。

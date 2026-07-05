-- ChatBI 只读角色 chatbi_ro (Plan4 v2 §4.2 六道闸第1道) —— 需超级用户执行一次, 幂等。
--
-- 用法 (容器内):
--   docker exec -i panse-system-db-1 psql -U panse -d panse_erp < backend/scripts/chatbi_create_ro.sql
-- 然后把 DSN 存进后台设置 (加密): key=chatbi_ro_dsn
--   postgresql+psycopg2://chatbi_ro:<改成真实密码>@db:5432/panse_erp
-- 未配置 chatbi_ro_dsn 时, executor 回退到主连接 + SET TRANSACTION READ ONLY (仍安全, 但不如独立角色贴身)。

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chatbi_ro') THEN
    CREATE ROLE chatbi_ro LOGIN PASSWORD 'CHANGE_ME_chatbi_ro';   -- ⚠改成强密码, 与 DSN 一致
  END IF;
END $$;

-- 只读: 收回一切, 只放行 schema usage + 三个白名单视图的 SELECT (连基表都不给)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM chatbi_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM chatbi_ro;
GRANT USAGE ON SCHEMA public TO chatbi_ro;
GRANT SELECT ON chatbi_v_orders, chatbi_v_products, chatbi_v_daily_sales TO chatbi_ro;

-- 角色级兜底: 超时 + 只读事务 (即使 executor 忘了设置也拦得住)
ALTER ROLE chatbi_ro SET statement_timeout = '10s';
ALTER ROLE chatbi_ro SET default_transaction_read_only = on;

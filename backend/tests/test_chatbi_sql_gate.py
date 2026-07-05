# -*- coding: utf-8 -*-
"""ChatBI 只读 SQL 闸门单测 —— 含 Plan4 v2 §9.3 红队清单。

红队全部必须被拒 (只读六道闸的 AST 层)。合法 SELECT 必须通过且被注入 LIMIT。
"""
import pytest

from app.chatbi.sql_gate import (
    MAX_ROWS,
    SqlGateError,
    is_readonly_select,
    validate_readonly_select,
)

ALLOWED = {"chatbi_v_orders", "chatbi_v_products", "chatbi_v_daily_sales"}


# ------------------------------- 合法查询 ------------------------------- #

def test_simple_select_passes_and_injects_limit():
    r = validate_readonly_select(
        "SELECT product_name, SUM(paid_amount) AS rev FROM chatbi_v_orders GROUP BY product_name",
        ALLOWED,
    )
    assert "chatbi_v_orders" in r.tables
    assert r.limited_to == MAX_ROWS
    assert "LIMIT" in r.safe_sql.upper()


def test_existing_small_limit_kept():
    r = validate_readonly_select("SELECT * FROM chatbi_v_orders LIMIT 10", ALLOWED)
    assert r.limited_to == 10


def test_oversized_limit_clamped():
    r = validate_readonly_select("SELECT * FROM chatbi_v_orders LIMIT 99999999", ALLOWED)
    assert r.limited_to == MAX_ROWS


def test_valid_cte_passes():
    # CTE 别名 t 不算表; 底层 chatbi_v_orders 在白名单 → 通过
    sql = ("WITH t AS (SELECT product_name, paid_amount FROM chatbi_v_orders) "
           "SELECT product_name, SUM(paid_amount) FROM t GROUP BY product_name")
    r = validate_readonly_select(sql, ALLOWED)
    assert "chatbi_v_orders" in r.tables
    assert "t" not in r.tables


def test_join_two_whitelisted_views():
    sql = ("SELECT o.product_name FROM chatbi_v_orders o "
           "JOIN chatbi_v_products p ON o.product_code = p.code")
    r = validate_readonly_select(sql, ALLOWED)
    assert set(r.tables) == {"chatbi_v_orders", "chatbi_v_products"}


def test_union_of_whitelisted_passes():
    sql = ("SELECT product_name FROM chatbi_v_orders "
           "UNION SELECT name FROM chatbi_v_products")
    r = validate_readonly_select(sql, ALLOWED)
    assert "chatbi_v_orders" in r.tables and "chatbi_v_products" in r.tables


# ------------------------------- 红队 (§9.3) ------------------------------- #

@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE chatbi_v_orders",                      # 多语句/分号注入
    "SELECT * FROM chatbi_v_orders; SELECT * FROM chatbi_v_products",
    "UPDATE chatbi_v_orders SET paid_amount = 0",                # 写操作
    "DELETE FROM chatbi_v_orders",
    "DROP TABLE chatbi_v_orders",
    "WITH x AS (DELETE FROM chatbi_v_orders RETURNING *) SELECT * FROM x",  # data-modifying CTE
    "SELECT * INTO tmp FROM chatbi_v_orders",                    # SELECT INTO
    "SELECT * FROM chatbi_v_orders FOR UPDATE",                  # 锁子句
    "SELECT * FROM users",                                       # 白名单外 (用户表)
    "SELECT password FROM users",
    "SELECT * FROM system_settings",                            # 白名单外 (含密钥)
    "SELECT * FROM pg_catalog.pg_tables",                        # 跨 schema / catalog 窥探
    "SELECT * FROM information_schema.columns",
    "TRUNCATE TABLE chatbi_v_orders",
    "GRANT SELECT ON chatbi_v_orders TO evil",
    "((( not valid sql at all",                                  # 语法错误
])
def test_red_team_all_rejected(sql):
    assert is_readonly_select(sql, ALLOWED) is False
    with pytest.raises(SqlGateError):
        validate_readonly_select(sql, ALLOWED)


def test_error_has_readable_reason():
    with pytest.raises(SqlGateError) as ei:
        validate_readonly_select("SELECT * FROM users", ALLOWED)
    assert ei.value.reason
    assert "users" in ei.value.reason

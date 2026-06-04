"""清空业务数据: 业务表被清, 账号/设置/配置保留。"""
from sqlalchemy import text

from app.models.auth import User
from app.models.order import Order
from app.services import data_reset_service


def test_reset_clears_business_keeps_accounts(db_session):
    # seed: 账号 + 业务数据 + 设置
    db_session.add(User(username="admin", password_hash="h", role="admin", is_active=True))
    db_session.add(Order(platform="淘宝", order_no="O1", qty=1))
    db_session.execute(text(
        "INSERT INTO system_settings (key, value_plain, is_secret) VALUES ('thr', '90', 0)"
    ))
    db_session.commit()

    deleted = data_reset_service.reset_business_data(db_session)
    assert deleted["orders"] == 1

    # 业务数据被清
    assert db_session.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 0
    # 账号 + 设置保留
    assert db_session.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    assert db_session.execute(text("SELECT COUNT(*) FROM system_settings")).scalar() == 1


def test_reset_table_list_excludes_protected():
    tables = set(data_reset_service.list_business_tables())
    # 这些绝不能出现在清空清单里
    for protected in ("users", "system_settings", "feishu_table_bindings",
                      "feishu_sync_map", "alembic_version"):
        assert protected not in tables, f"{protected} 不应被清空"


def test_reset_clears_fk_referenced_tables(db_session):
    """回归: materials 被 part_inventory 外键引用时, 两张表都要被清干净。

    历史 bug: 删 materials 触发外键冲突 → Postgres 事务 abort → commit 变 rollback
    → 全部数据回滚("清空后数据全在")。多轮 savepoint 删除应能彻底清空。
    """
    from app.models.material import Material

    db_session.add(Material(code="AC-0071", name="脚垫", price=None))
    db_session.execute(text(
        "INSERT INTO part_inventory (warehouse, material_code, physical_qty, locked_qty) "
        "VALUES ('杭州', 'AC-0071', 5, 0)"
    ))
    db_session.commit()

    data_reset_service.reset_business_data(db_session)

    assert db_session.execute(text("SELECT COUNT(*) FROM materials")).scalar() == 0
    assert db_session.execute(text("SELECT COUNT(*) FROM part_inventory")).scalar() == 0


def test_order_accessory_items_in_reset_list():
    # 新增的订单配件清单表必须纳入清空清单, 且排在 orders 之前 (子表先删)
    tables = data_reset_service.list_business_tables()
    assert "order_accessory_items" in tables
    assert tables.index("order_accessory_items") < tables.index("orders")

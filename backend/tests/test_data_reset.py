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

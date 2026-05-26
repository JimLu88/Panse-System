"""Phase 9 Tier 2: 平台同步 / 物流面单 / 动态安全库存 / 客户 CRM."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.customer import Customer
from app.models.inventory import PartInventory
from app.models.inventory_lock import InventoryLockLedger
from app.models.material import Material
from app.models.order import Order
from app.services import (
    customer_service,
    inventory_alert_service,
    platform_sync_service,
    shipping_label_service,
)


# ============================ 平台同步 (Mock) ============================ #


def test_platform_sync_mock_returns_empty(db_session):
    """Mock adapter 没配置 → 跳过, 不报错."""
    r = platform_sync_service.sync_all_platforms(db_session, since_hours=1)
    assert r["total_inserted"] == 0
    assert r["per_platform"]["taobao"]["configured"] is False


def test_register_custom_adapter_works(db_session):
    """业务: 注册一个测试 adapter, 拉到订单应 ingest."""
    class FakeAdapter(platform_sync_service.PlatformAdapter):
        name = "fake"
        def is_configured(self, db): return True
        def list_new_orders(self, db, since):
            return [platform_sync_service.PlatformOrder(
                platform="fake", order_no="FK001",
                customer_name="测试", qty=1,
                paid_amount=Decimal("500"),
            )]
        def push_tracking(self, db, order_no, tracking_no, carrier):
            return True

    platform_sync_service.register_adapter("fake", FakeAdapter())
    r = platform_sync_service.sync_all_platforms(db_session)
    assert r["per_platform"]["fake"]["inserted"] == 1
    o = db_session.execute(select(Order).where(Order.order_no == "FK001")).scalar_one()
    assert o.customer_name == "测试"


def test_platform_sync_dedupes(db_session):
    """已存在的 order_no 不重复入."""
    db_session.add(Order(platform="淘宝", order_no="DUP1", qty=1, status="paid"))
    db_session.flush()
    class A(platform_sync_service.PlatformAdapter):
        name = "x"
        def is_configured(self, db): return True
        def list_new_orders(self, db, since):
            return [platform_sync_service.PlatformOrder(
                platform="淘宝", order_no="DUP1",
            )]
        def push_tracking(self, db, *args): return True
    platform_sync_service.register_adapter("dup_test", A())
    r = platform_sync_service.sync_all_platforms(db_session)
    assert r["per_platform"]["dup_test"]["inserted"] == 0


# ============================ 物流面单 (Mock) ============================ #


def test_print_label_basic(db_session):
    o = Order(platform="淘宝", order_no="L1", qty=1, status="paid",
              customer_name="X", customer_phone="138",
              customer_address="北京")
    db_session.add(o); db_session.flush()
    label = shipping_label_service.print_label(db_session, order_id=o.id)
    assert label.tracking_no
    assert label.carrier
    # 回填到订单
    assert o.tracking_no == label.tracking_no


def test_print_label_missing_address_fails(db_session):
    o = Order(platform="淘宝", order_no="L2", qty=1, status="paid")
    db_session.add(o); db_session.flush()
    with pytest.raises(ValueError):
        shipping_label_service.print_label(db_session, order_id=o.id)


# ============================ 动态安全库存 ============================ #


def test_dynamic_safety_stock_with_history(db_session):
    """有历史出货 → 安全库存反映均值 + 标准差."""
    db_session.add(Material(code="M1", name="x", lead_time_days=10))
    db_session.add(PartInventory(warehouse="default", material_code="M1",
                                  physical_qty=Decimal("100")))
    # 模拟 60 天每天 5 件消耗
    for i in range(20):
        db_session.add(InventoryLockLedger(
            source_kind="factory_order", source_id=None,
            material_code="M1", kind="consume",
            qty=Decimal("5"), actor="system",
        ))
    db_session.flush()
    ss = inventory_alert_service.compute_dynamic_safety_stock(
        db_session, "M1", lead_time_days=10,
    )
    # 至少有正数 (平均 × 10 天)
    assert ss > 0


def test_dynamic_safety_no_history_uses_fallback(db_session):
    """无历史 → 退化为 lead_time 件."""
    db_session.add(Material(code="M1", name="x", lead_time_days=15))
    db_session.flush()
    ss = inventory_alert_service.compute_dynamic_safety_stock(
        db_session, "M1", lead_time_days=15,
    )
    assert ss == 15.0


def test_dynamic_safety_zero_lead_time(db_session):
    db_session.add(Material(code="M1", name="x"))
    db_session.flush()
    assert inventory_alert_service.compute_dynamic_safety_stock(
        db_session, "M1", lead_time_days=0,
    ) == 0.0


# ============================ 客户 CRM ============================ #


def test_aggregate_creates_customer(db_session):
    today = date.today()
    db_session.add_all([
        Order(platform="淘宝", order_no="C1", order_date=today,
              customer_name="张三", customer_phone="13800001111",
              qty=1, paid_amount=Decimal("1000"), status="signed"),
        Order(platform="淘宝", order_no="C2", order_date=today,
              customer_name="张三", customer_phone="13800001111",
              qty=1, paid_amount=Decimal("3000"), status="signed"),
    ])
    db_session.flush()
    r = customer_service.aggregate_all(db_session)
    assert r["customer_count"] == 1
    c = db_session.execute(select(Customer)).scalar_one()
    assert c.total_orders == 2
    assert c.total_revenue == Decimal("4000")
    assert c.tier == "bronze"   # < 5000


def test_tier_promotion(db_session):
    today = date.today()
    db_session.add(Order(platform="淘宝", order_no="V1", order_date=today,
                          customer_name="高客", customer_phone="13800002222",
                          qty=1, paid_amount=Decimal("60000"), status="signed"))
    db_session.flush()
    customer_service.aggregate_all(db_session)
    c = db_session.execute(select(Customer)).scalar_one()
    assert c.tier == "platinum"   # > 50000


def test_aggregate_skips_historical(db_session):
    today = date.today()
    db_session.add(Order(platform="淘宝", order_no="H1", order_date=today,
                          customer_name="X", customer_phone="138",
                          qty=1, paid_amount=Decimal("999999"),
                          status="signed", is_historical=True))
    db_session.flush()
    r = customer_service.aggregate_all(db_session)
    assert r["customer_count"] == 0


def test_aftersales_increments_returns(db_session):
    today = date.today()
    db_session.add(Order(platform="淘宝", order_no="R1", order_date=today,
                          customer_name="X", customer_phone="138",
                          qty=1, paid_amount=Decimal("100"), status="aftersales"))
    db_session.flush()
    customer_service.aggregate_all(db_session)
    c = db_session.execute(select(Customer)).scalar_one()
    assert c.total_returns == 1

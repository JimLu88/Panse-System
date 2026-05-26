"""19 项业务需求自检 e2e test.

每个 test 覆盖一个业务需求, 跑通 happy path 即视为已实现.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models.alert import Alert
from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.order import FactoryOrder, Order, PartPurchase
from app.models.product import Product
from app.services import (
    alert_service,
    asset_service,
    factory_order_service,
    inventory_alert_service,
    inventory_lock_service,
    order_service,
    return_service,
    sales_analytics,
    scheduler as scheduler_service,
    vision_ocr_service,
)
from app.services.ai_provider import AiResponse


# ----------------------------- 业务需求 1: 千牛截图 → 订单 -------- #


def test_req1_qianniu_screenshot_to_order(db_session):
    """业务需求 1: 千牛截图 OCR → 订单字段 → 入库"""
    fake_resp = """{
      "orders": [{
        "order_no": "Q001", "platform": "淘宝", "product_name": "电视柜",
        "qty": 1, "paid_amount": 999, "customer_name": "测试"
      }],
      "ocr_warnings": []
    }"""
    class P:
        name, model = "x", "x"
        def chat_with_image(self, **kw):
            return AiResponse(text=fake_resp, model="x")
    with patch("app.services.vision_ocr_service.build_provider", return_value=P()):
        data = vision_ocr_service.parse_qianniu_order(db_session, b"img", mime="image/png")
    assert data["orders"][0]["order_no"] == "Q001"


# ----------------------------- 业务需求 2: 订单 → 工厂下单表 ----- #


def test_req2_order_to_factory_order(db_session):
    db_session.add_all([
        Material(code="M1", name="木方"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("2")),
        PartInventory(warehouse="default", material_code="M1", physical_qty=10),
    ])
    db_session.flush()
    o = Order(platform="淘宝", order_no="OF1", product_code="P1", qty=1,
              status="pending_payment")
    db_session.add(o); db_session.flush()
    fo, lock = factory_order_service.generate_factory_order_for(db_session, o)
    assert fo.factory_order_no.startswith("F")
    assert fo.source_order_id == o.id


# ----------------------------- 业务需求 3: 锁库存 + 取消释放 ----- #


def test_req3_lock_inventory_and_release(db_session):
    db_session.add_all([
        Material(code="M1", name="x"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("2")),
        PartInventory(warehouse="default", material_code="M1", physical_qty=10),
    ])
    db_session.flush()
    o = Order(platform="淘宝", order_no="O3", product_code="P1", qty=1,
              status="pending_payment")
    db_session.add(o); db_session.flush()
    # paid → 锁
    order_service.transition(db_session, o, "paid")
    inv = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M1")
    ).scalar_one()
    assert inv.locked_qty == 2
    # cancelled → 释放
    order_service.transition(db_session, o, "cancelled")
    db_session.refresh(inv)
    assert inv.locked_qty == 0


# ----------------------------- 业务需求 4: 缺货 alert ------------- #


def test_req4_shortage_alert(db_session):
    db_session.add_all([
        Material(code="M1", name="x"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("5")),
        PartInventory(warehouse="default", material_code="M1", physical_qty=2),
    ])
    db_session.flush()
    o = Order(platform="淘宝", order_no="O4", product_code="P1", qty=1,
              status="pending_payment")
    db_session.add(o); db_session.flush()
    factory_order_service.generate_factory_order_for(db_session, o)
    crit = db_session.execute(
        select(Alert).where(Alert.severity == "critical", Alert.kind == "low_stock_part")
    ).scalars().all()
    assert len(crit) >= 1


# ----------------------------- 业务需求 5: 持续弹窗 (sticky) ------ #


def test_req5_sticky_alert_persists(db_session):
    a = alert_service.upsert(
        db_session, kind="low_stock_part", severity="critical",
        title="缺货", dedupe_key="low_stock_part:M1", sticky=True,
    )
    assert a.sticky is True
    # Active 列表里有它
    actives = alert_service.list_active(db_session)
    assert any(x.id == a.id for x in actives)


# ----------------------------- 业务需求 6: 进货单截图 + 快递 ------ #


def test_req6_purchase_screenshot_and_tracking(db_session):
    fake = """{
      "purchase": {
        "supplier_name": "S1", "purchase_date": "2026-05-01",
        "tracking_no": "SF888",
        "lines": [{"material_name": "木方", "qty": 10, "unit_price": 25}]
      }
    }"""
    class P:
        name, model = "x", "x"
        def chat_with_image(self, **kw):
            return AiResponse(text=fake, model="x")
    with patch("app.services.vision_ocr_service.build_provider", return_value=P()):
        data = vision_ocr_service.parse_purchase_invoice(db_session, b"x", mime="image/png")
    assert data["purchase"]["tracking_no"] == "SF888"
    assert len(data["purchase"]["lines"]) == 1


def test_req6_missing_tracking_alert(db_session):
    db_session.add(PartPurchase(
        purchase_no="PP1", supplier="S1",
        purchase_date=date.today() - timedelta(days=2),
        material_code="M1", material_name="x",
    ))
    db_session.flush()
    r = factory_order_service.check_missing_tracking(db_session)
    assert r["missing_tracking_count"] >= 1


# ----------------------------- 业务需求 7: 销售预测 -------------- #


def test_req7_sales_forecast(db_session):
    today = date.today()
    for i in range(30):
        db_session.add(Order(
            platform="淘宝", order_no=f"S{i}",
            order_date=today - timedelta(days=i * 2),
            product_code="P1", sku_code="S1", qty=1,
            paid_amount=Decimal("100"), status="shipped",
        ))
    db_session.flush()
    forecast = sales_analytics.forecast_30d(db_session)
    s1 = next(r for r in forecast if r["sku"] == "S1")
    assert s1["forecast_30d"] > 0


# ----------------------------- 业务需求 8: 库存预警 + 滞销 ------ #


def test_req8_low_stock_and_slow_moving(db_session):
    db_session.add_all([
        Material(code="M1", name="x", lead_time_days=10, priority="high"),
        PartInventory(warehouse="default", material_code="M1",
                       physical_qty=2, last_outbound_at=date.today() - timedelta(days=90)),
    ])
    db_session.flush()
    n = inventory_alert_service.scan_low_stock(db_session)
    assert n == 1
    split = sales_analytics.slow_moving_split(db_session)
    assert any(x["material_code"] == "M1" for x in split["long_idle"])


def test_req8_manual_inventory_adjust(db_session):
    db_session.add(Material(code="M1", name="x"))
    db_session.add(PartInventory(warehouse="default", material_code="M1", physical_qty=10))
    db_session.flush()
    inventory_lock_service.manual_adjust(
        db_session, material_code="M1", new_physical=8,
        actor="alice", remark="盘点",
    )
    inv = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M1")
    ).scalar_one()
    assert inv.physical_qty == 8


# ----------------------------- 业务需求 9: 退货闭环 ------------- #


def test_req9_return_flow(db_session):
    o = Order(platform="淘宝", order_no="R9", product_code="P1",
              qty=1, status="signed")
    db_session.add(o); db_session.flush()
    a = return_service.create_return(db_session, order_no="R9", reason="x", tracking_no="T1")
    return_service.mark_received(db_session, a.id)
    return_service.confirm_return_inbound(db_session, a.id, product_code="P1", qty=1)
    assert a.second_inbound_confirmed == "是"
    pinv = db_session.execute(
        select(ProductInventory).where(ProductInventory.product_code == "P1")
    ).scalar_one()
    assert pinv.physical_qty == 1


def test_req9_disassemble_bom(db_session):
    db_session.add_all([
        Material(code="M1", name="x"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("3")),
        ProductInventory(warehouse="default", product_code="P1", physical_qty=2),
    ])
    db_session.flush()
    res = inventory_lock_service.disassemble_product_to_parts(
        db_session, product_code="P1", sku_code=None, qty=1,
    )
    assert res["product_remaining"] == 1
    inv = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M1")
    ).scalar_one()
    assert inv.physical_qty == 3


# ----------------------------- 业务需求 10: 远期订单 ----------- #


def test_req10_future_order_creation_and_activation(db_session, monkeypatch):
    from app import database as db_module
    bind = db_session.get_bind()
    from sqlalchemy.orm import sessionmaker
    LocalSm = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "SessionLocal", LocalSm)

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    fo = factory_order_service.create_future_order(
        db_session, base_order_no="X1", activate_at=past,
        product_code="P1", qty=1,
    )
    db_session.commit()
    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None
    scheduler_service.register_job(
        "act", "activate", scheduler_service._job_activate_future_orders,
        cron={"hour": 8, "minute": 0},
    )
    scheduler_service.trigger_now("act")
    db_session.expire_all()
    o = db_session.get(Order, fo.id)
    assert o.status == "paid"


# ----------------------------- 业务需求 11: 17:00 退款检查 ------ #


def test_req11_refund_check_job(db_session):
    o = Order(platform="淘宝", order_no="R11", status="aftersales",
              compensation_fee=Decimal("100"))
    db_session.add(o); db_session.flush()
    o.updated_at = datetime.now(timezone.utc) - timedelta(hours=48)
    db_session.flush()
    r = factory_order_service.check_refund_pending_orders(db_session)
    assert r["flagged"] >= 1


def test_req11_void_factory_order(db_session):
    db_session.add_all([
        Material(code="M1", name="x"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("1")),
        PartInventory(warehouse="default", material_code="M1", physical_qty=10),
    ])
    db_session.flush()
    o = Order(platform="淘宝", order_no="O11", product_code="P1", qty=1,
              status="pending_payment")
    db_session.add(o); db_session.flush()
    order_service.transition(db_session, o, "paid")
    fo = db_session.execute(
        select(FactoryOrder).where(FactoryOrder.source_order_id == o.id)
    ).scalar_one()
    factory_order_service.void_factory_order(db_session, fo.id, reason="测试作废")
    db_session.refresh(fo)
    assert fo.voided_at is not None


# ----------------------------- 业务需求 12: 定制下单 (现状 part) -- #


def test_req12_customization_exists(db_session):
    """业务需求 12 部分实现 (customization_service 已有 preview/confirm)."""
    from app.services import customization_service
    assert hasattr(customization_service, "preview")
    assert hasattr(customization_service, "confirm")


# ----------------------------- 业务需求 13: 自动核对 (定时) ------ #


def test_req13_reconcile_job_registered():
    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None
    scheduler_service._register_default_jobs()
    job_ids = {cfg["label"] for cfg in scheduler_service._REGISTRY.values()}
    assert "数据自动核对" in job_ids or any("核对" in lbl for lbl in job_ids)


# ----------------------------- 业务需求 14: 资产 + 饼图 ---------- #


def test_req14_asset_summary(db_session):
    db_session.add_all([
        Material(code="M1", name="x", price=Decimal("10")),
        PartInventory(warehouse="default", material_code="M1", physical_qty=20),
    ])
    db_session.flush()
    s = asset_service.summary(db_session)
    cat_names = {c.name for c in s.categories}
    assert "库存账面" in cat_names
    assert "账户余额" in cat_names
    assert "待发货资产" in cat_names


# ----------------------------- 业务需求 15: 店铺销售报表 -------- #


def test_req15_sales_summary_with_ranks(db_session):
    today = date.today()
    db_session.add_all([
        Order(platform="淘宝", order_no=f"X1", order_date=today,
              product_code="P1", qty=1, paid_amount=Decimal("1000"),
              actual_cost=Decimal("400"), status="shipped"),
        Order(platform="淘宝", order_no=f"X2", order_date=today,
              product_code="P2", qty=1, paid_amount=Decimal("500"),
              actual_cost=Decimal("100"), status="shipped"),
    ])
    db_session.flush()
    s = sales_analytics.summary(db_session, start=today - timedelta(days=1), end=today)
    assert s.order_count == 2
    assert len(s.top_products_by_profit) == 2
    assert len(s.top_products_by_profit_rate) == 2


# ----------------------------- 业务需求 16: 分产品销售报表 ------ #


def test_req16_breakdown_per_sku(db_session):
    today = date.today()
    db_session.add(Order(platform="淘宝", order_no="A1", order_date=today,
                          product_code="P1", sku_code="S1", qty=2,
                          paid_amount=Decimal("2000"), actual_cost=Decimal("800"),
                          status="shipped"))
    db_session.flush()
    rows = sales_analytics.product_breakdown(db_session,
                                             start=today - timedelta(days=1), end=today)
    assert len(rows) == 1
    assert rows[0]["qty"] == 2
    assert "gross_profit_rate" in rows[0]
    assert "net_profit_rate" in rows[0]


# ----------------------------- 业务需求 17: 经营状况分析 -------- #


def test_req17_period_summary_acts_as_income_expense(db_session):
    """业务需求 17 经营状况通过 sales_summary 在不同 period (7/30/月/年) 上跑出收支分析."""
    # 验证多 period 都返回有效值, 不报错
    today = date.today()
    db_session.add(Order(platform="淘宝", order_no="P17", order_date=today,
                          product_code="P1", qty=1, paid_amount=Decimal("500"),
                          actual_cost=Decimal("200"), status="shipped"))
    db_session.flush()
    for start_offset in [7, 30, 365]:
        s = sales_analytics.summary(db_session,
                                    start=today - timedelta(days=start_offset),
                                    end=today)
        assert s.order_count >= 1


# ----------------------------- 业务需求 18: 自动任务清单 -------- #


def test_req18_scheduler_lists_all_jobs():
    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None
    scheduler_service._register_default_jobs()
    jobs = scheduler_service.list_jobs()
    job_ids = {j["job_id"] for j in jobs}
    assert "daily_17_refund_check" in job_ids
    assert "daily_08_activate_future" in job_ids
    assert "daily_07_lowstock_scan" in job_ids
    assert "daily_10_data_reconcile" in job_ids


# ----------------------------- 业务需求 19: 公式对账 + 未核销 ---- #


def test_req19_formula_vs_unmatched_pool(db_session):
    from app.models.finance import AccountBalance, AlipayFlow
    db_session.add_all([
        AccountBalance(account_name="支付宝", period_year=2026, period_month=5,
                       opening_balance=Decimal("0"), income=Decimal("0"),
                       expense=Decimal("0"), closing_balance=Decimal("100")),
        AlipayFlow(account="ali", transaction_no="UN1",
                   transaction_time=datetime.now(),
                   counterparty="X", amount=Decimal("500"),
                   reconciliation_status="open"),
    ])
    db_session.flush()
    s = asset_service.summary(db_session)
    assert hasattr(s, "formula_a")
    assert hasattr(s, "formula_b")
    assert hasattr(s, "diff")
    rows = asset_service.unmatched_recent_flows(db_session, days=7)
    assert any(r["transaction_no"] == "UN1" for r in rows)

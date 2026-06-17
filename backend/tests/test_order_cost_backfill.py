# -*- coding: utf-8 -*-
"""#30 全自动成本兜底: 类目/全店成本率动态算 + 缺SKU成本订单按销售额×率兜底 + 写异常。"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.exception import DataException
from app.models.order import Order
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import order_cost_service as ocs


def test_category_ratio_sales_fallback_and_exception(db_session):
    db = db_session
    db.add(Product(code="PPS11111111111", name="测试餐桌", category="餐桌"))
    db.add(PricingSku(product_code="PPS11111111111", sku_code="S1", sku="基础",
                      accounting_cost=Decimal("600"), factory_cost=Decimal("500")))
    # 一笔有成本的正式单 → 餐桌成本率 = 600/1000 = 0.6
    db.add(Order(platform="淘宝", order_no="C1", product_code="PPS11111111111", sku_code="S1",
                 qty=1, order_date=date(2026, 6, 1), status="paid",
                 paid_amount=Decimal("1000"), theoretical_cost=Decimal("600")))
    # 一笔无产品编码、无成本的单 → 按 实付×率 兜底
    db.add(Order(platform="淘宝", order_no="N1", product_code=None,
                 qty=1, order_date=date(2026, 6, 2), status="paid",
                 paid_amount=Decimal("2000")))
    db.flush()

    ratios = ocs.category_cost_ratios(db, min_orders=1)
    assert ratios["_store"] == 0.6
    assert ratios.get("餐桌") == 0.6

    res = ocs.auto_cost_backfill(db)

    # N1 无编码 → 全店率0.6 兜底: 2000×0.6 = 1200
    n1 = db.execute(select(Order).where(Order.order_no == "N1")).scalar_one()
    assert n1.theoretical_cost is not None and float(n1.theoretical_cost) == 1200.0
    # C1 已有成本, 不动
    c1 = db.execute(select(Order).where(Order.order_no == "C1")).scalar_one()
    assert float(c1.theoretical_cost) == 600.0
    # 写了缺成本异常
    exc = db.execute(select(DataException).where(
        DataException.source_table == "orders",
        DataException.exception_type == "cost_missing_estimated",
        DataException.source_pk == "N1")).scalar_one_or_none()
    assert exc is not None and exc.status == "open"
    assert res["estimated_by_ratio"] >= 1


def test_estimated_exception_auto_resolves_when_actual_cost_entered(db_session):
    db = db_session
    db.add(Order(platform="淘宝", order_no="N2", product_code=None,
                 qty=1, order_date=date(2026, 6, 2), status="paid",
                 paid_amount=Decimal("1000")))
    # 给个有成本单撑起全店率
    db.add(Order(platform="淘宝", order_no="C2", product_code=None,
                 qty=1, order_date=date(2026, 6, 1), status="paid",
                 paid_amount=Decimal("1000"), theoretical_cost=Decimal("500")))
    db.flush()
    ocs.auto_cost_backfill(db)
    assert db.execute(select(DataException).where(
        DataException.source_pk == "N2", DataException.status == "open")).scalar_one_or_none()

    # 人工补了实际成本 → 再跑应自动关闭异常
    n2 = db.execute(select(Order).where(Order.order_no == "N2")).scalar_one()
    n2.actual_cost = Decimal("450")
    db.flush()
    ocs.auto_cost_backfill(db)
    ex = db.execute(select(DataException).where(DataException.source_pk == "N2")).scalar_one()
    assert ex.status == "resolved"

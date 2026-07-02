"""R5 半成品/白坯: 默认关闭时无计划; 打开 + 产品打标后出池化备货计划。"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.order import Order
from app.models.product import Product
from app.services import product_inventory_service, sales_analytics


def _sales(db, code, n=30):
    today = date.today()
    for i in range(n):
        db.add(Order(platform="淘宝", order_no=f"{code}o{i}",
                     order_date=today - timedelta(days=i * 2),
                     product_code=code, sku_code="S1", qty=1,
                     paid_amount=Decimal("100"), status="shipped", is_custom=False))
    db.flush()


def test_semi_finished_off_by_default(db_session):
    db = db_session
    adv = sales_analytics.stock_advice(db)
    assert adv["semi_finished_enabled"] is False
    assert adv["semi_finished"] == []


def test_semi_finished_on_produces_pooled_plan(db_session):
    db = db_session
    product_inventory_service.save_forecast_config(db, {"enable_semi_finished": True})
    # 两个共享白坯 WB1 的产品(前段共用, 后段个性化), 各自有销量
    db.add_all([
        Product(code="P1", name="榉木餐桌-原色", semi_finished_eligible=True, semi_group="WB1"),
        Product(code="P2", name="榉木餐桌-岩板", semi_finished_eligible=True, semi_group="WB1"),
    ])
    _sales(db, "P1")
    _sales(db, "P2")

    adv = sales_analytics.stock_advice(db)
    assert adv["semi_finished_enabled"] is True
    grp = next(g for g in adv["semi_finished"] if g["semi_group"] == "WB1")
    assert len(grp["members"]) == 2
    # 每款预测 18 → 池化 36; 现有/在产白坯 0 → 建议备 36
    assert grp["pooled_forecast"] == 36
    assert grp["recommend_semi"] == 36


def test_semi_finished_on_but_no_tagged_products(db_session):
    db = db_session
    product_inventory_service.save_forecast_config(db, {"enable_semi_finished": True})
    adv = sales_analytics.stock_advice(db)
    assert adv["semi_finished_enabled"] is True
    assert adv["semi_finished"] == []      # 没打标产品 → 空计划(前端提示去打标)

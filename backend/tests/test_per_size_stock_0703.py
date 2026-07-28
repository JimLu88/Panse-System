"""成品库存 推荐备货 按「每个尺寸自己的销量」算, 不再让同产品各尺寸共用产品总日均。

修前: 榉木餐桌4个尺寸各用产品总日均(1.82) → 各推~132、合计527(卖1.82/天却囤9个月)。
修后: 按 sku 名里的尺寸口令(1.4米/1.6米…)匹配订单 sku, 各尺寸按自己销量, 合计回到合理量。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.inventory import ProductInventory
from app.models.order import Order
from app.services import product_inventory_service as pis


def _orders(db, sku_desc, n, code="P1"):
    today = date.today()
    for i in range(n):
        db.add(Order(platform="淘宝", order_no=f"{sku_desc}{i}",
                     order_date=today - timedelta(days=(i * 60 // max(n, 1))),
                     product_code=code, sku_code=f"{code}x", sku=sku_desc, qty=1,
                     paid_amount=Decimal("100"), status="shipped", is_custom=False))
    db.flush()


def test_recommend_uses_per_size_sales(db_session):
    db = db_session
    _orders(db, "砂白1.4米餐桌", 6)     # 1.4米 少
    _orders(db, "砂白1.6米餐桌", 30)    # 1.6米 多
    inv14 = ProductInventory(warehouse="default", product_code="P1", sku="餐桌-1.4米",
                             physical_qty=Decimal("0"))
    inv16 = ProductInventory(warehouse="default", product_code="P1", sku="餐桌-1.6米",
                             physical_qty=Decimal("0"))
    db.add_all([inv14, inv16]); db.flush()
    cfg = pis.get_forecast_config(db)
    abc = pis.compute_abc_map(db, cfg)
    split = pis.compute_in_production_split(db)
    s14 = pis.compute_product_stats(db, inv14, abc_map=abc, cfg=cfg, in_production_split=split)
    s16 = pis.compute_product_stats(db, inv16, abc_map=abc, cfg=cfg, in_production_split=split)

    product_daily = pis._compute_daily_sales(db, "P1", None, cfg=cfg)   # 产品总日均(两尺寸合计)
    # 1.4米 不再用产品总日均, 明显更小
    assert s14["daily_sales_30d"] < product_daily
    # 卖得多的 1.6米 日均更高、推荐更多
    assert s16["daily_sales_30d"] > s14["daily_sales_30d"]
    assert s16["auto_reorder_qty"] >= s14["auto_reorder_qty"]


def test_no_size_token_falls_back_to_product_level(db_session):
    """sku 名里没有尺寸口令 → 退回产品级日均(向后兼容)。"""
    db = db_session
    _orders(db, "无尺寸款", 20)
    inv = ProductInventory(warehouse="default", product_code="P1", sku="主款",
                           physical_qty=Decimal("0"))
    db.add(inv); db.flush()
    cfg = pis.get_forecast_config(db)
    abc = pis.compute_abc_map(db, cfg)
    split = pis.compute_in_production_split(db)
    st = pis.compute_product_stats(db, inv, abc_map=abc, cfg=cfg, in_production_split=split)
    product_daily = pis._compute_daily_sales(db, "P1", None, cfg=cfg)
    assert abs(st["forecast_daily"] - product_daily) < 1e-6


def test_size_token_extraction():
    assert pis._size_token("榉木餐桌-1.4米") == "1.4米"
    assert pis._size_token("砂白色1.6米岩板餐桌") == "1.6米"
    assert pis._size_token("主款") is None
    assert pis._size_token(None) is None

"""R1 (ATP 真实缺口): 备货建议 / 成品库存推荐 要扣掉「在产/在途」(已下工厂未到货)。

场景: 预测 18 件, 现货 3 件, 在产 5 件 → 需生产 = max(18 − 3 − 5, 0) = 10 (旧口径会算 15)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.bom import BomLine
from app.models.inventory import ProductInventory
from app.models.material import Material
from app.models.order import FactoryOrder, Order
from app.services import product_inventory_service, sales_analytics


def _seed_regular_sales(db, code="P1", n=30):
    """60 天内 30 单(每 2 天 1 单) → 预测 18 件(0.5/天 ×30 ×1.2)。"""
    today = date.today()
    for i in range(n):
        db.add(Order(
            platform="淘宝", order_no=f"R{code}{i}",
            order_date=today - timedelta(days=i * 2),
            product_code=code, sku_code="S1", qty=1,
            paid_amount=Decimal("100"), status="shipped", is_custom=False,
        ))
    db.flush()


def test_stock_advice_subtracts_in_production(db_session):
    db = db_session
    db.add_all([
        Material(code="M1", name="木方", lead_time_days=10, priority="high"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("2")),
    ])
    _seed_regular_sales(db, "P1")   # 预测 18
    db.add(ProductInventory(warehouse="default", product_code="P1", sku="主款",
                            physical_qty=Decimal("3")))
    # 在产 5 件(未到货未作废)
    db.add(FactoryOrder(factory_order_no="FO_IP", product_code="P1", qty=5,
                        order_date=date.today() - timedelta(days=2), actual_delivery=None))
    db.flush()

    advice = sales_analytics.stock_advice(db)
    p = next(x for x in advice["products"] if x["product_code"] == "P1")
    assert p["forecast_30d"] == 18
    assert p["in_stock"] == 3
    assert p["in_production"] == 5
    assert p["need_to_produce"] == 10   # 18 − 3 − 5 (旧口径=15)

    # 物料需求跟着降: 需生产 10 件 × BOM 2 = 20; 无库存 → missing 20
    m = next(x for x in advice["materials"] if x["material_code"] == "M1")
    assert m["need_qty"] == 20


def test_in_production_ignores_delivered_and_voided(db_session):
    db = db_session
    _seed_regular_sales(db, "P1")   # 预测 18
    db.add(ProductInventory(warehouse="default", product_code="P1", sku="主款",
                            physical_qty=Decimal("0")))
    db.add_all([
        # 已到货 → 不算在产
        FactoryOrder(factory_order_no="FO_done", product_code="P1", qty=4,
                     order_date=date.today() - timedelta(days=20),
                     actual_delivery=date.today() - timedelta(days=1)),
        # 已作废 → 不算在产
        FactoryOrder(factory_order_no="FO_void", product_code="P1", qty=7,
                     order_date=date.today() - timedelta(days=5),
                     actual_delivery=None, voided_at=datetime.now(timezone.utc)),
        # 真·在产 6 件
        FactoryOrder(factory_order_no="FO_live", product_code="P1", qty=6,
                     order_date=date.today() - timedelta(days=2), actual_delivery=None),
    ])
    db.flush()
    advice = sales_analytics.stock_advice(db)
    p = next(x for x in advice["products"] if x["product_code"] == "P1")
    assert p["in_production"] == 6          # 只算 live 的 6
    assert p["need_to_produce"] == 12       # 18 − 0 − 6


def test_custom_segment_does_not_subtract_in_production(db_session):
    """定制段(接单再产)保守: 不减在产, 全量倒推通用料。"""
    db = db_session
    db.add(Material(code="MC", name="通用双面胶", lead_time_days=3, is_custom=False))
    db.add(BomLine(product_code="PC", material_code="MC", qty_per_product=Decimal("1")))
    today = date.today()
    for i in range(30):
        db.add(Order(platform="淘宝", order_no=f"C{i}",
                     order_date=today - timedelta(days=i * 2),
                     product_code="PC", sku_code="S1", qty=1,
                     paid_amount=Decimal("100"), status="shipped", is_custom=True))
    db.add(FactoryOrder(factory_order_no="FO_pc", product_code="PC", qty=5,
                        order_date=today - timedelta(days=2), actual_delivery=None))
    db.flush()
    advice = sales_analytics.stock_advice(db)
    cp = next(x for x in advice["custom_products"] if x["product_code"] == "PC")
    assert cp["in_production"] == 0            # 定制段不减在产
    assert cp["need_to_produce"] == cp["forecast_30d"]


def test_product_stats_recommend_subtracts_in_production(db_session):
    """成品库存页: A 类「推荐备货」也扣在产, 避免对在做的量重复下单。"""
    db = db_session
    _seed_regular_sales(db, "P1")          # 主销 30 单
    _seed_regular_sales(db, "P2", n=8)     # 陪衬 8 单 → P1 累计占比进 A 线 → A 类
    inv = ProductInventory(warehouse="default", product_code="P1", sku="主款",
                           physical_qty=Decimal("1"))
    db.add(inv)
    db.add(FactoryOrder(factory_order_no="FO_ip2", product_code="P1", qty=8,
                        order_date=date.today() - timedelta(days=1), actual_delivery=None))
    db.flush()
    cfg = product_inventory_service.get_forecast_config(db)
    abc = product_inventory_service.compute_abc_map(db, cfg)
    ip = product_inventory_service.compute_in_production_map(db)
    with_ip = product_inventory_service.compute_product_stats(
        db, inv, abc_map=abc, cfg=cfg, in_production_map=ip)
    without_ip = product_inventory_service.compute_product_stats(
        db, inv, abc_map=abc, cfg=cfg, in_production_map={})
    assert with_ip["abc_class"] == "A"
    assert with_ip["in_production"] == 8
    assert with_ip["auto_reorder_qty"] <= without_ip["auto_reorder_qty"]
    # 原推荐若 ≥8, 扣在产后恰好少 8
    if without_ip["auto_reorder_qty"] >= 8:
        assert without_ip["auto_reorder_qty"] - with_ip["auto_reorder_qty"] == 8

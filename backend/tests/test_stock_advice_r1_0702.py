"""R1 (ATP 真实缺口, 区分已占用): 只扣「自由在产(备货单)」, 客户单在产另列不抵。

场景: 预测 18, 现货 3, 自由在产 5 → 需生产 = max(18−3−5, 0) = 10。
客户单(MTO, source_order_id 有值)在产 不参与抵扣, 仅在 in_production_allocated 展示。
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


def test_free_in_production_subtracts_allocated_does_not(db_session):
    db = db_session
    # 本测试只验 R1 在产抵扣口径, 关掉季节缩放以免预测数被目标月系数放大/压缩
    product_inventory_service.save_forecast_config(db, {"enable_seasonal": False})
    db.add_all([
        Material(code="M1", name="木方", lead_time_days=10, priority="high"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("2")),
    ])
    _seed_regular_sales(db, "P1")   # 预测 18
    db.add(ProductInventory(warehouse="default", product_code="P1", sku="主款",
                            physical_qty=Decimal("3")))
    db.add_all([
        # 自由在产(备货单, 无 source_order_id): 5 件 → 抵扣
        FactoryOrder(factory_order_no="FO_FREE", product_code="P1", qty=5,
                     order_date=date.today() - timedelta(days=2), actual_delivery=None),
        # 客户单在产(MTO, 有 source_order_id): 40 件 → 不抵扣, 仅展示
        FactoryOrder(factory_order_no="FO_MTO", product_code="P1", qty=40,
                     order_date=date.today() - timedelta(days=2), actual_delivery=None,
                     source_order_id=123),
    ])
    db.flush()

    advice = sales_analytics.stock_advice(db)
    p = next(x for x in advice["products"] if x["product_code"] == "P1")
    assert p["in_stock"] == 3
    assert p["in_production_free"] == 5          # 只有备货单算
    assert p["in_production_allocated"] == 40     # 客户单单独列, 不抵
    assert p["need_to_produce"] == max(p["forecast_30d"] - 3 - 5, 0)

    m = next(x for x in advice["materials"] if x["material_code"] == "M1")
    assert m["need_qty"] == p["need_to_produce"] * 2


def test_in_production_ignores_delivered_and_voided(db_session):
    db = db_session
    product_inventory_service.save_forecast_config(db, {"enable_seasonal": False})  # 只验在产口径
    _seed_regular_sales(db, "P1")   # 预测 18
    db.add(ProductInventory(warehouse="default", product_code="P1", sku="主款",
                            physical_qty=Decimal("0")))
    db.add_all([
        FactoryOrder(factory_order_no="FO_done", product_code="P1", qty=4,
                     order_date=date.today() - timedelta(days=20),
                     actual_delivery=date.today() - timedelta(days=1)),      # 已到货, 不算
        FactoryOrder(factory_order_no="FO_void", product_code="P1", qty=7,
                     order_date=date.today() - timedelta(days=5),
                     actual_delivery=None, voided_at=datetime.now(timezone.utc)),  # 作废, 不算
        FactoryOrder(factory_order_no="FO_live", product_code="P1", qty=6,
                     order_date=date.today() - timedelta(days=2), actual_delivery=None),  # 自由在产 6
    ])
    db.flush()
    advice = sales_analytics.stock_advice(db)
    p = next(x for x in advice["products"] if x["product_code"] == "P1")
    assert p["in_production_free"] == 6
    assert p["need_to_produce"] == max(p["forecast_30d"] - 6, 0)


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
    assert cp["in_production_free"] == 0
    assert cp["need_to_produce"] == cp["forecast_30d"]


def test_product_stats_recommend_subtracts_free_not_allocated(db_session):
    """成品库存页: 推荐备货只扣自由在产, 客户单在产另列不抵。"""
    db = db_session
    _seed_regular_sales(db, "P1")
    _seed_regular_sales(db, "P2", n=8)     # 陪衬 → P1 进 A 类
    inv = ProductInventory(warehouse="default", product_code="P1", sku="主款",
                           physical_qty=Decimal("1"))
    db.add(inv)
    db.add_all([
        FactoryOrder(factory_order_no="FO_free2", product_code="P1", qty=8,
                     order_date=date.today() - timedelta(days=1), actual_delivery=None),
        FactoryOrder(factory_order_no="FO_mto2", product_code="P1", qty=30,
                     order_date=date.today() - timedelta(days=1), actual_delivery=None,
                     source_order_id=77),
    ])
    db.flush()
    cfg = product_inventory_service.get_forecast_config(db)
    abc = product_inventory_service.compute_abc_map(db, cfg)
    split = product_inventory_service.compute_in_production_split(db)
    with_ip = product_inventory_service.compute_product_stats(
        db, inv, abc_map=abc, cfg=cfg, in_production_split=split)
    none_ip = product_inventory_service.compute_product_stats(
        db, inv, abc_map=abc, cfg=cfg, in_production_split=({}, {}))
    assert with_ip["abc_class"] == "A"
    assert with_ip["in_production_free"] == 8
    assert with_ip["in_production_allocated"] == 30
    # 只扣自由在产 8 (不是 8+30=38)
    if none_ip["auto_reorder_qty"] >= 8:
        assert none_ip["auto_reorder_qty"] - with_ip["auto_reorder_qty"] == 8

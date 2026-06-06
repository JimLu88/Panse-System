"""配件库存智能预警 compute_part_stats 单测 (纯计算, 无需 DB)."""
from decimal import Decimal

from app.models.inventory import PartInventory
from app.services.part_inventory_service import (
    compute_material_daily_consumption,
    compute_part_stats,
)


def _pi(**kw):
    return PartInventory(warehouse="江西仓库", material_code="AC-0001", **kw)


def test_danger_when_below_reorder():
    inv = _pi(physical_qty=Decimal("3"), locked_qty=Decimal("0"),
              avg_daily_sales=Decimal("1"), lead_time_days=10, safety_stock=Decimal("5"))
    s = compute_part_stats(inv)
    assert s["reorder_point_computed"] == 15          # 5 + 10×1
    assert s["warning_status"] == "danger"            # 可用 3 < 15
    assert s["auto_reorder_qty"] == 27                # 15×2 − 3


def test_critical_when_zero():
    inv = _pi(physical_qty=Decimal("0"), locked_qty=Decimal("0"))
    assert compute_part_stats(inv)["warning_status"] == "critical"


def test_excess_when_slow_moving():
    inv = _pi(physical_qty=Decimal("100"), locked_qty=Decimal("0"),
              avg_daily_sales=Decimal("1"), slow_moving_days=60,
              safety_stock=Decimal("1"), lead_time_days=1)
    s = compute_part_stats(inv)
    assert s["days_of_stock"] == 100.0
    assert s["warning_status"] == "excess"            # 100 天 > 60 天


def test_ok_when_healthy():
    inv = _pi(physical_qty=Decimal("30"), locked_qty=Decimal("0"),
              avg_daily_sales=Decimal("1"), slow_moving_days=60,
              safety_stock=Decimal("2"), lead_time_days=2)
    assert compute_part_stats(inv)["warning_status"] == "ok"


def test_no_daily_falls_back_to_available_vs_safety():
    inv = _pi(physical_qty=Decimal("3"), locked_qty=Decimal("0"), safety_stock=Decimal("5"))
    s = compute_part_stats(inv)
    assert s["days_of_stock"] is None                 # 无日均销量 → 不算天数
    assert s["warning_status"] == "danger"            # 可用 3 < 安全线 5


def test_compute_part_stats_uses_auto_daily():
    """传入自动日均消耗 → 优先用它, source=auto。"""
    inv = _pi(physical_qty=Decimal("5"), locked_qty=Decimal("0"), avg_daily_sales=Decimal("0"))
    s = compute_part_stats(inv, daily_consumption=1.0)
    assert s["daily_sales"] == 1.0
    assert s["daily_source"] == "auto"
    assert s["days_of_stock"] == 5.0


# ---- 日均消耗 = 订单×BOM 自动计算 (需 DB) ----
from datetime import date  # noqa: E402

from app.models.bom import BomLine  # noqa: E402
from app.models.material import Material  # noqa: E402
from app.models.order import Order  # noqa: E402
from app.models.pricing import PricingSku  # noqa: E402


def test_daily_consumption_from_orders_and_bom(db_session):
    """订单数量 × BOM单产品用量 ÷ 天数 = 日均消耗 (订单直给 sku_code)。"""
    db_session.add(Material(code="M1", name="物料1"))
    db_session.add(BomLine(product_code="P1", sku="规格A", sku_code="SKU-A",
                           material_code="M1", qty_per_product=2))
    db_session.add(Order(platform="淘宝", order_no="O1", sku_code="SKU-A", qty=5,
                         status="signed", order_date=date.today(),
                         is_historical=False, is_refill=False))
    db_session.flush()
    cons = compute_material_daily_consumption(db_session, days=10)
    assert cons.get("M1") == 1.0                       # 5件×2 / 10天


def test_consumption_via_sku_name_fallback(db_session):
    """订单无 sku_code → 用 SKU 名去定价表反查 sku_code 再展开 BOM。"""
    db_session.add(Material(code="M2", name="物料2"))
    db_session.add(PricingSku(product_code="P2", sku="规格B", sku_code="SKU-B"))
    db_session.add(BomLine(product_code="P2", sku="规格B", sku_code="SKU-B",
                           material_code="M2", qty_per_product=3))
    db_session.add(Order(platform="淘宝", order_no="O2", sku="规格B", qty=2,
                         status="shipped", order_date=date.today(),
                         is_historical=False, is_refill=False))
    db_session.flush()
    cons = compute_material_daily_consumption(db_session, days=30)
    assert cons.get("M2") == 0.2                       # 2件×3 / 30天


def test_refill_orders_excluded_from_consumption(db_session):
    """补单(刷单)不耗真实库存 → 不计入消耗。"""
    db_session.add(Material(code="M3", name="物料3"))
    db_session.add(BomLine(product_code="P3", sku_code="SKU-C",
                           material_code="M3", qty_per_product=1))
    db_session.add(Order(platform="淘宝", order_no="O3", sku_code="SKU-C", qty=9,
                         status="signed", order_date=date.today(),
                         is_historical=False, is_refill=True))
    db_session.flush()
    assert "M3" not in compute_material_daily_consumption(db_session, days=10)

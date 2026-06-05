"""配件库存智能预警 compute_part_stats 单测 (纯计算, 无需 DB)."""
from decimal import Decimal

from app.models.inventory import PartInventory
from app.services.part_inventory_service import compute_part_stats


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

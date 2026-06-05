"""配件库存智能分析 — 给配件库存算 可用量/库存天数/低库存预警/补货建议。

口径与成品库存一致 (见 product_inventory_service), 但配件的「日均消耗 / 提前期 /
滞销天数 / 安全库存」直接取库内值 (由导入维护: avg_daily_sales / lead_time_days /
slow_moving_days / safety_stock), 不再从订单历史现推 —— 配件消耗要走 BOM 反查,
成本高且口径多变, 先用用户维护的值, 后续可在此扩展按 BOM 估算。

warning_status:
  critical — 可用量 ≤ 0
  danger   — 可用量 < 预警线(reorder_point)
  warning  — 库存天数 < 滞销阈值/2 (快用完)
  excess   — 库存天数 > 滞销天数 (滞销/积压)
  ok       — 正常
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import PartInventory

_DEFAULT_SLOW_MOVING_DAYS = 60


def compute_part_stats(inv: PartInventory) -> dict:
    """单条配件库存的推算字段 (纯计算, 不查库, 不写库)。"""
    daily = float(inv.avg_daily_sales or 0)
    lead_time = inv.lead_time_days
    slow_days = inv.slow_moving_days or _DEFAULT_SLOW_MOVING_DAYS
    safety = float(inv.safety_stock or 0)
    if safety == 0 and lead_time and daily > 0:
        safety = lead_time * daily * 1.5
    reorder_pt = safety + (lead_time or 0) * daily
    available = float(inv.available_qty)

    days_of_stock: Optional[float] = round(available / daily, 1) if daily > 0 else None

    if available <= 0:
        status = "critical"
    elif reorder_pt > 0 and available < reorder_pt:
        status = "danger"
    elif days_of_stock is not None and days_of_stock < slow_days / 2:
        status = "warning"
    elif days_of_stock is not None and days_of_stock > slow_days:
        status = "excess"
    else:
        status = "ok"

    auto_reorder = max(0.0, reorder_pt * 2 - available) if reorder_pt > 0 else 0.0

    return {
        "available_qty": round(available, 2),
        "daily_sales": daily,
        "lead_time_days": lead_time,
        "slow_moving_days": slow_days,
        "safety_stock_computed": round(safety, 2),
        "reorder_point_computed": round(reorder_pt, 2),
        "days_of_stock": days_of_stock,
        "warning_status": status,
        "auto_reorder_qty": round(auto_reorder, 0),
    }


def list_with_stats(
    db: Session, *, warehouse: Optional[str] = None, material_code: Optional[str] = None,
    limit: int = 200, offset: int = 0,
) -> list[tuple[PartInventory, dict]]:
    """配件库存列表 + 每条的推算字段。"""
    stmt = select(PartInventory)
    if warehouse:
        stmt = stmt.where(PartInventory.warehouse == warehouse)
    if material_code:
        stmt = stmt.where(PartInventory.material_code == material_code)
    stmt = stmt.order_by(PartInventory.id.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [(r, compute_part_stats(r)) for r in rows]

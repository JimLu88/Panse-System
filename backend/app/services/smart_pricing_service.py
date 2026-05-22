"""智能定价建议 (Phase 10, 业务需求 8.3 进阶).

基于 成本 + 目标利润率 + 库存压力 + 历史成交价 给出建议价.

公开:
    suggest_price(db, sku_code | product_code, target_margin) -> SuggestResult

简化版: 不接竞品价 (后续接口可扩).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.order import Order


@dataclass
class PriceSuggestion:
    sku_code: Optional[str]
    product_code: str
    cost: float                       # BOM 物料 + 工艺成本
    historical_avg_price: float       # 过去 30 天成交均价
    target_margin: float              # 目标毛利率 (0-1)
    suggested_price: float            # 综合建议
    inventory_pressure: float         # 库存压力调节系数 (库存高 → 价低)
    notes: list[str]


def _bom_cost(db: Session, product_code: str) -> Decimal:
    rows = db.execute(
        select(BomLine, Material.price.label("p")).join(
            Material, Material.code == BomLine.material_code,
        ).where(BomLine.product_code == product_code)
    ).all()
    total = Decimal("0")
    for line, p in rows:
        if p:
            total += Decimal(line.qty_per_product or 0) * Decimal(p)
    return total


def _historical_avg(db: Session, product_code: str, sku_code: Optional[str], days=30) -> Decimal:
    cutoff = date.today() - timedelta(days=days)
    q = select(Order).where(
        Order.product_code == product_code,
        Order.order_date >= cutoff,
        Order.status.in_(("paid", "shipped", "signed")),
    )
    if sku_code:
        q = q.where(Order.sku_code == sku_code)
    orders = db.execute(q).scalars().all()
    if not orders:
        return Decimal("0")
    total = Decimal("0")
    count = 0
    for o in orders:
        if o.paid_amount and o.qty:
            total += Decimal(o.paid_amount) / Decimal(o.qty)
            count += 1
    return total / count if count > 0 else Decimal("0")


def _inventory_pressure(db: Session, product_code: str) -> Decimal:
    """库存压力系数 0-1: 库存高于 30 天预测 N 倍时调低价格.

    返回 1.0 - 0.1 * (ratio - 1), 即 ratio=2 → 0.9, ratio=4 → 0.7. 最低 0.7.
    """
    inv = db.execute(
        select(ProductInventory).where(
            ProductInventory.product_code == product_code,
        )
    ).scalar_one_or_none()
    if not inv or inv.physical_qty <= 0:
        return Decimal("1.0")
    from app.services import sales_analytics
    forecast_list = sales_analytics.forecast_30d(db)
    forecast = next(
        (f["forecast_30d"] for f in forecast_list if f["product_code"] == product_code),
        0,
    )
    if forecast <= 0:
        return Decimal("1.0")
    ratio = Decimal(inv.physical_qty) / Decimal(forecast)
    if ratio <= 1:
        return Decimal("1.0")
    factor = Decimal("1.0") - Decimal("0.1") * (ratio - 1)
    return max(factor, Decimal("0.7"))


def suggest_price(
    db: Session, *, product_code: str, sku_code: Optional[str] = None,
    target_margin: float = 0.35,
) -> PriceSuggestion:
    cost = _bom_cost(db, product_code)
    hist = _historical_avg(db, product_code, sku_code)
    pressure = _inventory_pressure(db, product_code)

    notes: list[str] = []
    # 基础价 = 成本 / (1 - 目标毛利率)
    if target_margin >= 1 or target_margin < 0:
        target_margin = 0.35
    base_target = cost / Decimal(1 - target_margin) if cost > 0 else Decimal("0")

    # 综合: 历史均价 50% + 目标价 30% + 库存压力调节 20%
    if hist > 0 and base_target > 0:
        blended = hist * Decimal("0.5") + base_target * Decimal("0.5")
        notes.append(f"历史均价 {hist:.2f} 与目标价 {base_target:.2f} 加权.")
    elif hist > 0:
        blended = hist
        notes.append("无成本数据, 用历史均价.")
    elif base_target > 0:
        blended = base_target
        notes.append("无历史数据, 用 cost / (1 - margin) 算.")
    else:
        blended = Decimal("0")
        notes.append("无成本无历史, 无法估算.")

    final = blended * pressure
    if pressure < 1:
        notes.append(f"库存压力调节 ×{float(pressure):.2f} (库存偏高 → 降价促销).")

    return PriceSuggestion(
        sku_code=sku_code, product_code=product_code,
        cost=float(cost), historical_avg_price=float(hist),
        target_margin=target_margin,
        suggested_price=float(final.quantize(Decimal("0.01"))),
        inventory_pressure=float(pressure),
        notes=notes,
    )

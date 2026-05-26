"""订单理论成本反推 (按 BOM × 物料单价).

- 理论成本(单件) = Σ(该 SKU 的 BOM 每条 qty_per_product × Material.price).
- 实际成本不反推, 仅由人工/导入录入.
- 差异 = 实际成本 − 理论成本 (任一缺失则 None), 由 Order.cost_diff 属性给出.
- compute() 返回逐条物料 breakdown, 供前端把反推过程可视化.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order
from app.models.pricing import PricingSku

_CENTS = Decimal("0.01")


@dataclass
class CostLine:
    material_code: str
    material_name: Optional[str]
    qty_per_product: Decimal
    unit_price: Optional[Decimal]
    line_cost: Optional[Decimal]      # qty_per_product × unit_price
    missing_price: bool


@dataclass
class CostBreakdown:
    order_no: str
    sku_code: Optional[str]
    qty: int
    unit_cost: Decimal                # 单件理论成本 = Σ line_cost
    total_cost: Decimal               # unit_cost × qty
    lines: list[CostLine] = field(default_factory=list)
    resolved: bool = False            # 是否成功匹配到 BOM
    missing_price_count: int = 0
    note: Optional[str] = None


def _resolve_sku_code(db: Session, order: Order) -> Optional[str]:
    """订单上 sku_code 优先; 否则用 SKU 名去定价表反查 sku_code (与制单图同逻辑)."""
    if order.sku_code:
        return order.sku_code
    if order.sku:
        ps = db.execute(
            select(PricingSku).where(PricingSku.sku == order.sku)
        ).scalar_one_or_none()
        if ps:
            return ps.sku_code
    return None


def compute(db: Session, order: Order) -> CostBreakdown:
    """反推一条订单的理论成本, 返回明细 (不写库)."""
    sku_code = _resolve_sku_code(db, order)
    lines: list[CostLine] = []
    if sku_code:
        rows = db.execute(
            select(BomLine, Material.name.label("mat_name"), Material.price.label("price"))
            .join(Material, BomLine.material_code == Material.code, isouter=True)
            .where(BomLine.sku_code == sku_code)
        ).all()
        for bom, mat_name, price in rows:
            qty_per = Decimal(str(bom.qty_per_product or 1))
            p = Decimal(str(price)) if price is not None else None
            line_cost = (qty_per * p).quantize(_CENTS) if p is not None else None
            lines.append(CostLine(
                material_code=bom.material_code,
                material_name=mat_name,
                qty_per_product=qty_per,
                unit_price=p,
                line_cost=line_cost,
                missing_price=p is None,
            ))

    unit_cost = sum(
        (ln.line_cost for ln in lines if ln.line_cost is not None), Decimal("0")
    ).quantize(_CENTS)
    qty = int(order.qty or 1)
    total_cost = (unit_cost * qty).quantize(_CENTS)
    missing = sum(1 for ln in lines if ln.missing_price)

    if not lines:
        note = "未匹配到 BOM (订单缺 sku_code 或该 SKU 无 BOM), 无法反推理论成本"
    elif missing:
        note = f"{missing} 项物料缺单价, 已按 0 计入, 请到物料表补价后重算"
    else:
        note = None

    return CostBreakdown(
        order_no=order.order_no,
        sku_code=sku_code,
        qty=qty,
        unit_cost=unit_cost,
        total_cost=total_cost,
        lines=lines,
        resolved=bool(lines),
        missing_price_count=missing,
        note=note,
    )


def recompute_and_save(db: Session, order: Order) -> CostBreakdown:
    """反推并把单件理论成本写回 order.theoretical_cost (不动 actual_cost)."""
    bd = compute(db, order)
    if bd.resolved:
        order.theoretical_cost = bd.unit_cost
    return bd


def recompute_all(db: Session, *, only_missing: bool = True) -> dict:
    """批量反推. only_missing=True 时只补 theoretical_cost 为空的订单.

    Returns: {updated, skipped_no_bom, total}
    """
    stmt = select(Order)
    if only_missing:
        stmt = stmt.where(Order.theoretical_cost.is_(None))
    orders = db.execute(stmt).scalars().all()
    updated = skipped = 0
    for o in orders:
        bd = recompute_and_save(db, o)
        if bd.resolved:
            updated += 1
        else:
            skipped += 1
    return {"updated": updated, "skipped_no_bom": skipped, "total": len(orders)}

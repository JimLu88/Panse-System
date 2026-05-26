"""可生产数计算 (plan §4 ProducibilityService.compute()).

给定某个 SKU 编码，回答：
    - 成品库存里现成有多少件能直接发？
    - 配件库存 + BOM 还能再造多少件？瓶颈在哪个物料？
    - 缺料清单（要造 N 件还差多少哪些料）？

算法：
    1. 查 4a 成品库存 (按 sku 或 product_code) → in_stock_qty
    2. 查 BOM 行 by sku_code → 每个物料的 qty_per_product
    3. 对每个物料：
         可制造件数 = floor(配件可用库存 / qty_per_product)
       SKU 整体可制造 = 各物料件数的 min（瓶颈物料决定上限）
    4. 缺料清单 = 想造 target 件时，对每个物料：
         缺 = max(0, target * qty_per_product - 配件可用库存)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material


@dataclass
class MaterialRequirement:
    material_code: str
    material_name: Optional[str]
    qty_per_product: Decimal
    available_stock: Decimal
    can_build_units: int          # 仅看这一种料能造多少件
    shortage_for_target: Decimal  # 目标件数下缺的量（target=0 时 = 0）


@dataclass
class ProducibilityReport:
    sku_code: Optional[str]
    product_code: Optional[str]
    target_qty: int
    in_stock_qty: int             # 4a 成品库存
    can_build_qty: int            # min(各物料的 can_build_units)
    total_available_qty: int      # in_stock + can_build
    bottleneck: Optional[MaterialRequirement] = None
    requirements: list[MaterialRequirement] = field(default_factory=list)
    missing_for_target: list[MaterialRequirement] = field(default_factory=list)


def _sum_part_available(db: Session, material_code: str) -> Decimal:
    rows = db.execute(
        select(PartInventory.physical_qty, PartInventory.locked_qty).where(
            PartInventory.material_code == material_code
        )
    ).all()
    total = Decimal("0")
    for physical, locked in rows:
        total += Decimal(int(physical or 0) - int(locked or 0))
    return total


def _sum_product_in_stock(db: Session, *, sku_code: Optional[str], product_code: Optional[str]) -> int:
    stmt = select(ProductInventory.physical_qty, ProductInventory.locked_qty)
    if sku_code:
        stmt = stmt.where(ProductInventory.sku == sku_code)  # 4a 表里 sku 列存的是 sku 名
    elif product_code:
        stmt = stmt.where(ProductInventory.product_code == product_code)
    else:
        return 0
    total = 0
    for physical, locked in db.execute(stmt).all():
        total += int(physical or 0) - int(locked or 0)
    return max(total, 0)


def compute(
    db: Session,
    *,
    sku_code: Optional[str] = None,
    product_code: Optional[str] = None,
    target_qty: int = 1,
) -> ProducibilityReport:
    if not sku_code and not product_code:
        raise ValueError("sku_code 和 product_code 至少需要一个")
    if target_qty < 0:
        raise ValueError("target_qty must be >= 0")

    # 1. 现成库存
    in_stock = _sum_product_in_stock(db, sku_code=sku_code, product_code=product_code)

    # 2. BOM 行
    stmt = select(BomLine, Material.name.label("mat_name")).join(
        Material, BomLine.material_code == Material.code, isouter=True
    )
    if sku_code:
        stmt = stmt.where(BomLine.sku_code == sku_code)
    else:
        stmt = stmt.where(BomLine.product_code == product_code)
    bom_rows = db.execute(stmt).all()

    requirements: list[MaterialRequirement] = []
    can_build_units = None  # None = no BOM rows → 不能定量评估造的部分

    for line, mat_name in bom_rows:
        qty_per = Decimal(line.qty_per_product or 0)
        if qty_per <= 0:
            # qty_per_product 缺失或 0：跳过但保留一条提醒
            requirements.append(MaterialRequirement(
                material_code=line.material_code,
                material_name=mat_name,
                qty_per_product=qty_per,
                available_stock=_sum_part_available(db, line.material_code),
                can_build_units=0,
                shortage_for_target=Decimal("0"),
            ))
            continue
        avail = _sum_part_available(db, line.material_code)
        units = int(avail / qty_per) if avail >= 0 else 0
        shortage = max(Decimal("0"), Decimal(target_qty) * qty_per - avail)
        req = MaterialRequirement(
            material_code=line.material_code,
            material_name=mat_name,
            qty_per_product=qty_per,
            available_stock=avail,
            can_build_units=units,
            shortage_for_target=shortage,
        )
        requirements.append(req)
        can_build_units = units if can_build_units is None else min(can_build_units, units)

    bottleneck: Optional[MaterialRequirement] = None
    if requirements and can_build_units is not None:
        # 瓶颈 = can_build_units 最小的那条（在 BOM 行里）
        candidates = [r for r in requirements if r.qty_per_product > 0]
        if candidates:
            bottleneck = min(candidates, key=lambda r: r.can_build_units)

    can_build = can_build_units or 0
    missing = [r for r in requirements if r.shortage_for_target > 0]

    return ProducibilityReport(
        sku_code=sku_code,
        product_code=product_code,
        target_qty=target_qty,
        in_stock_qty=in_stock,
        can_build_qty=can_build,
        total_available_qty=in_stock + can_build,
        bottleneck=bottleneck,
        requirements=requirements,
        missing_for_target=missing,
    )

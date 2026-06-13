"""库存服务。

add_part_row(): 录入一条配件库存。如果传入的是物料名称且该名称不存在，自动建定制物料。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import PartInventory
from app.models.material import Material
from app.services import material_service


@dataclass
class AddPartRowResult:
    inventory: PartInventory
    material: Material
    material_created: bool


def add_part_row(
    db: Session,
    *,
    warehouse: str,
    material_code: Optional[str] = None,
    material_name: Optional[str] = None,
    physical_qty: int = 0,
    locked_qty: int = 0,
    spec: Optional[str] = None,
    unit: Optional[str] = None,
    remark: Optional[str] = None,
) -> AddPartRowResult:
    if not warehouse:
        raise ValueError("warehouse is required")
    if not material_code and not material_name:
        raise ValueError("material_code or material_name is required")

    material_created = False
    material: Optional[Material] = None

    if material_code:
        material = db.execute(
            select(Material).where(Material.code == material_code)
        ).scalar_one_or_none()
        if material is None and not material_name:
            raise ValueError(f"material_code {material_code} not found and no material_name supplied")

    if material is None:
        # 走名称分支：精确匹配 + 缺则建定制
        result = material_service.ensure_by_name(db, material_name or "")
        material = result.material
        material_created = result.created

    # (warehouse, material_code) 唯一 (迁移 0074): 二次入库累加数量, 不再重复插行
    inv = db.execute(
        select(PartInventory).where(
            PartInventory.warehouse == warehouse,
            PartInventory.material_code == material.code,
        )
    ).scalar_one_or_none()
    if inv is not None:
        inv.physical_qty = Decimal(inv.physical_qty or 0) + Decimal(physical_qty or 0)
        inv.locked_qty = Decimal(inv.locked_qty or 0) + Decimal(locked_qty or 0)
        if spec:
            inv.spec = spec
        if unit:
            inv.unit = unit
        if remark:
            inv.remark = remark
        db.flush()
        return AddPartRowResult(inventory=inv, material=material, material_created=material_created)

    inv = PartInventory(
        warehouse=warehouse,
        material_code=material.code,
        spec=spec,
        unit=unit or material.unit,
        physical_qty=physical_qty,
        locked_qty=locked_qty,
        remark=remark,
    )
    db.add(inv)
    db.flush()
    return AddPartRowResult(inventory=inv, material=material, material_created=material_created)

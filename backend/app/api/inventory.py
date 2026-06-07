from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.inventory import PartInventory
from app.models.material import Material
from app.schemas.inventory import (
    PartInventoryAddResponse, PartInventoryCreate, PartInventoryOut, PartInventoryWithStats,
)
from app.services import (
    inventory_service, part_defect_service, part_inventory_service, part_return_service,
)


class PartInventoryPatch(BaseModel):
    physical_qty: Optional[Decimal] = None
    locked_qty: Optional[Decimal] = None
    remark: Optional[str] = None

router = APIRouter(prefix="/api/inventory/parts", tags=["inventory"])


def _to_out(inv: PartInventory) -> PartInventoryOut:
    return PartInventoryOut(
        id=inv.id,
        warehouse=inv.warehouse,
        material_code=inv.material_code,
        spec=inv.spec,
        unit=inv.unit,
        physical_qty=inv.physical_qty,
        locked_qty=inv.locked_qty,
        defective_qty=inv.defective_qty,
        available_qty=inv.available_qty,
        remark=inv.remark,
    )


@router.get("", response_model=list[PartInventoryOut])
def list_part_inventory(
    warehouse: Optional[str] = None,
    material_code: Optional[str] = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(PartInventory)
    if warehouse:
        stmt = stmt.where(PartInventory.warehouse == warehouse)
    if material_code:
        stmt = stmt.where(PartInventory.material_code == material_code)
    stmt = stmt.order_by(PartInventory.id.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/with-stats", response_model=list[PartInventoryWithStats])
def list_part_inventory_with_stats(
    warehouse: Optional[str] = None,
    material_code: Optional[str] = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """配件库存 + 实时预警 (可用量/库存天数/低库存预警/补货建议)。

    日均消耗/提前期/滞销天数 取库内导入值; 据此算预警线、库存天数、预警状态、补货建议。
    """
    out: list[PartInventoryWithStats] = []
    for inv, stats in part_inventory_service.list_with_stats(
        db, warehouse=warehouse, material_code=material_code, limit=limit, offset=offset,
    ):
        base = _to_out(inv).model_dump()
        out.append(PartInventoryWithStats(
            **base,
            daily_sales=stats["daily_sales"],
            daily_source=stats["daily_source"],
            lead_time_days=stats["lead_time_days"],
            slow_moving_days=stats["slow_moving_days"],
            safety_stock_computed=stats["safety_stock_computed"],
            reorder_point_computed=stats["reorder_point_computed"],
            days_of_stock=stats["days_of_stock"],
            warning_status=stats["warning_status"],
            auto_reorder_qty=stats["auto_reorder_qty"],
        ))
    return out


@router.post("", response_model=PartInventoryAddResponse, status_code=201)
def add_part_inventory_row(payload: PartInventoryCreate, db: Session = Depends(get_db)):
    try:
        result = inventory_service.add_part_row(
            db,
            warehouse=payload.warehouse,
            material_code=payload.material_code,
            material_name=payload.material_name,
            physical_qty=payload.physical_qty,
            locked_qty=payload.locked_qty,
            spec=payload.spec,
            unit=payload.unit,
            remark=payload.remark,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e

    db.commit()
    db.refresh(result.inventory)
    # also fetch material for code/name pair
    mat = db.execute(select(Material).where(Material.code == result.material.code)).scalar_one()
    return PartInventoryAddResponse(
        inventory=_to_out(result.inventory),
        material_code=mat.code,
        material_name=mat.name,
        material_created=result.material_created,
    )


@router.patch("/{inventory_id}", response_model=PartInventoryOut)
def update_part_inventory(
    inventory_id: int,
    payload: PartInventoryPatch,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """盘库调整: 修改配件库存数量 (physical_qty)."""
    inv = db.get(PartInventory, inventory_id)
    if not inv:
        raise HTTPException(404, "inventory row not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(inv, k, v)
    db.commit()
    db.refresh(inv)
    return _to_out(inv)


# ----------------------------- 坏件 / 返厂维修 (方案B) ----------------- #


class DefectMarkIn(BaseModel):
    qty: Decimal
    reason: str = "到货不良"
    remark: Optional[str] = None


class DefectResolveIn(BaseModel):
    qty: Decimal
    disposition: str   # repaired(回良品) / scrapped(报废) / returned(退货退款)
    # 财务闭环 (方案C, 均可选): 金额 = 退款应收/维修费/报废损失; 供应商/原采购单/返厂快递单号
    amount: Optional[Decimal] = None
    supplier: Optional[str] = None
    related_purchase_no: Optional[str] = None
    tracking_no: Optional[str] = None
    reason: Optional[str] = None
    remark: Optional[str] = None


@router.post("/{inventory_id}/defect", response_model=PartInventoryOut)
def mark_part_defective(
    inventory_id: int,
    payload: DefectMarkIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """标记坏件: 把配件从良品库移到「待返厂/维修中」(不计入可用), 写台账留痕."""
    inv = db.get(PartInventory, inventory_id)
    if not inv:
        raise HTTPException(404, "inventory row not found")
    try:
        part_defect_service.mark_defective(
            db, material_code=inv.material_code, qty=payload.qty,
            actor=getattr(user, "username", None) or "user",
            reason=payload.reason, remark=payload.remark, warehouse=inv.warehouse,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(inv)
    return _to_out(inv)


@router.post("/{inventory_id}/defect/resolve", response_model=PartInventoryOut)
def resolve_part_defective(
    inventory_id: int,
    payload: DefectResolveIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """处理待返厂坏件: repaired 回良品 / scrapped 报废 / returned 退货退款.

    同步生成一条返厂台账 (part_returns) 记录这次处置的钱, 供应商对账用 (方案C)。
    """
    inv = db.get(PartInventory, inventory_id)
    if not inv:
        raise HTTPException(404, "inventory row not found")
    try:
        part_return_service.record_resolution(
            db, material_code=inv.material_code, qty=payload.qty,
            disposition=payload.disposition, warehouse=inv.warehouse,
            actor=getattr(user, "username", None) or "user",
            amount=payload.amount, supplier=payload.supplier,
            related_purchase_no=payload.related_purchase_no,
            tracking_no=payload.tracking_no, reason=payload.reason,
            remark=payload.remark,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(inv)
    return _to_out(inv)

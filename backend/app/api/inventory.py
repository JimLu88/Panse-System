from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.inventory import PartInventory
from app.models.material import Material
from app.schemas.inventory import PartInventoryAddResponse, PartInventoryCreate, PartInventoryOut
from app.services import inventory_service

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

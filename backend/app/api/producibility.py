from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import producibility_service

router = APIRouter(prefix="/api/producibility", tags=["producibility"])


class MaterialRequirementOut(BaseModel):
    material_code: str
    material_name: Optional[str]
    qty_per_product: Decimal
    available_stock: Decimal
    can_build_units: int
    shortage_for_target: Decimal


class ProducibilityOut(BaseModel):
    sku_code: Optional[str]
    product_code: Optional[str]
    target_qty: int
    in_stock_qty: int
    can_build_qty: int
    total_available_qty: int
    bottleneck: Optional[MaterialRequirementOut]
    requirements: list[MaterialRequirementOut]
    missing_for_target: list[MaterialRequirementOut]


def _to_out(req) -> MaterialRequirementOut:
    return MaterialRequirementOut(**req.__dict__)


@router.get("", response_model=ProducibilityOut)
def compute(
    sku_code: Optional[str] = Query(None),
    product_code: Optional[str] = Query(None),
    target_qty: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if not sku_code and not product_code:
        raise HTTPException(400, "sku_code 或 product_code 至少传一个")
    try:
        r = producibility_service.compute(
            db, sku_code=sku_code, product_code=product_code, target_qty=target_qty
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return ProducibilityOut(
        sku_code=r.sku_code,
        product_code=r.product_code,
        target_qty=r.target_qty,
        in_stock_qty=r.in_stock_qty,
        can_build_qty=r.can_build_qty,
        total_available_qty=r.total_available_qty,
        bottleneck=_to_out(r.bottleneck) if r.bottleneck else None,
        requirements=[_to_out(req) for req in r.requirements],
        missing_for_target=[_to_out(req) for req in r.missing_for_target],
    )

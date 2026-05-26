from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import quote_service

router = APIRouter(prefix="/api/quotes", tags=["quotes"])


class LightQuoteOut(BaseModel):
    sku_code: str
    sku: Optional[str]
    size_category: Optional[str]
    list_price: Optional[Decimal]
    daily_price: Optional[Decimal]
    small_promo: Optional[Decimal]
    mid_promo: Optional[Decimal]
    big_promo: Optional[Decimal]
    big_promo_margin: Optional[Decimal]
    gross_margin_rate: Optional[Decimal]


@router.get("/light/{sku_code}", response_model=LightQuoteOut)
def light(sku_code: str, db: Session = Depends(get_db)):
    q = quote_service.light_lookup(db, sku_code)
    if q is None:
        raise HTTPException(404, f"sku {sku_code} not priced")
    return LightQuoteOut(**q.__dict__)


class HighQuoteIn(BaseModel):
    cost: Decimal = Field(..., gt=0)
    size_category: str
    margin_rate: Optional[Decimal] = None


class HighQuoteOut(BaseModel):
    cost: Decimal
    size_category: str
    margin_rate: Decimal
    final_price: Decimal
    margin_amount: Decimal


@router.post("/high", response_model=HighQuoteOut)
def high(payload: HighQuoteIn):
    try:
        q = quote_service.high_calc(
            cost=payload.cost,
            size_category=payload.size_category,
            margin_rate=payload.margin_rate,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return HighQuoteOut(**q.__dict__)


class MaterialSwapIn(BaseModel):
    from_code: str
    to_code: str
    qty: Decimal = Decimal("1")


class MaterialSwapOut(BaseModel):
    from_code: str
    to_code: str
    qty: Decimal
    from_unit_price: Optional[Decimal]
    to_unit_price: Optional[Decimal]
    delta: Optional[Decimal]


@router.post("/material-swap", response_model=MaterialSwapOut)
def material_swap(payload: MaterialSwapIn, db: Session = Depends(get_db)):
    try:
        r = quote_service.material_swap_delta(
            db, from_code=payload.from_code, to_code=payload.to_code, qty=payload.qty
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return MaterialSwapOut(**r.__dict__)


class DimensionDeltaIn(BaseModel):
    base_cm: Decimal
    target_cm: Decimal
    per_cm_cost: Decimal = Field(..., ge=0)
    margin_rate: Decimal = Decimal("0.15")


class DimensionDeltaOut(BaseModel):
    base_cm: Decimal
    target_cm: Decimal
    cm_diff: Decimal
    per_cm_cost: Decimal
    margin_rate: Decimal
    delta: Decimal


@router.post("/dimension", response_model=DimensionDeltaOut)
def dimension(payload: DimensionDeltaIn):
    try:
        delta = quote_service.any_dimension_delta(
            base_cm=payload.base_cm,
            target_cm=payload.target_cm,
            per_cm_cost=payload.per_cm_cost,
            margin_rate=payload.margin_rate,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return DimensionDeltaOut(
        base_cm=payload.base_cm,
        target_cm=payload.target_cm,
        cm_diff=payload.target_cm - payload.base_cm,
        per_cm_cost=payload.per_cm_cost,
        margin_rate=payload.margin_rate,
        delta=delta,
    )

"""智能定价 + 异常自动诊断 API (Phase 10)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.services import exception_diagnosis_service, shipping_label_service, smart_pricing_service

router = APIRouter(prefix="/api", tags=["smart"])


class PriceSuggestionOut(BaseModel):
    sku_code: Optional[str]
    product_code: str
    cost: float
    historical_avg_price: float
    target_margin: float
    suggested_price: float
    inventory_pressure: float
    notes: list[str]


@router.get("/smart-pricing/suggest", response_model=PriceSuggestionOut)
def suggest_price(
    product_code: str,
    sku_code: Optional[str] = None,
    target_margin: float = 0.35,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    s = smart_pricing_service.suggest_price(
        db, product_code=product_code, sku_code=sku_code, target_margin=target_margin,
    )
    return PriceSuggestionOut(
        sku_code=s.sku_code, product_code=s.product_code,
        cost=s.cost, historical_avg_price=s.historical_avg_price,
        target_margin=s.target_margin,
        suggested_price=s.suggested_price,
        inventory_pressure=s.inventory_pressure,
        notes=s.notes,
    )


@router.get("/exceptions/{exception_id}/diagnose")
def diagnose(
    exception_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        return exception_diagnosis_service.diagnose(db, exception_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ----------------------------- 物流面单 -------------------------- #


class ShippingLabelOut(BaseModel):
    tracking_no: str
    carrier: str
    label_url: str


@router.post("/orders/{order_id}/print-label", response_model=ShippingLabelOut)
def print_label(
    order_id: int,
    carrier: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        label = shipping_label_service.print_label(
            db, order_id=order_id, carrier=carrier,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return ShippingLabelOut(
        tracking_no=label.tracking_no, carrier=label.carrier,
        label_url=label.label_url,
    )

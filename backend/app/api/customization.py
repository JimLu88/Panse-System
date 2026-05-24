"""尺寸微定制 API (业务需求 §2)."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import customization_ai_service, customization_service

router = APIRouter(prefix="/api/customization", tags=["customization"])


class DiffLineOut(BaseModel):
    material_code: str
    material_name: Optional[str]
    original_qty: Decimal
    new_qty: Decimal
    note: Optional[str]
    requires_new_material: bool = False


class PreviewOut(BaseModel):
    base_sku_code: str
    proposed_custom_sku_code: str
    dimension_changes: dict
    diff_lines: list[DiffLineOut]


class PreviewIn(BaseModel):
    base_sku_code: str = Field(..., min_length=3)
    dimension_changes: dict = Field(
        ..., description="如 {长: 2000, 宽: 400} (单位 mm), 不变的可不传"
    )


@router.post("/preview", response_model=PreviewOut)
def preview(payload: PreviewIn, db: Session = Depends(get_db)):
    try:
        r = customization_service.preview(
            db,
            base_sku_code=payload.base_sku_code,
            dimension_changes=payload.dimension_changes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return PreviewOut(
        base_sku_code=r.base_sku_code,
        proposed_custom_sku_code=r.proposed_custom_sku_code,
        dimension_changes=r.dimension_changes,
        diff_lines=[DiffLineOut(**d.__dict__) for d in r.diff_lines],
    )


class ConfirmIn(BaseModel):
    base_sku_code: str
    dimension_changes: dict
    order_no: Optional[str] = None
    note: Optional[str] = None
    qty_overrides: Optional[dict[str, Decimal]] = None  # material_code → 新数量


class ConfirmOut(BaseModel):
    custom_variant_id: int
    custom_sku_code: str
    cloned_bom_lines: int


class PriceBreakdownItemOut(BaseModel):
    label: str
    amount: float
    note: str = ""


class AiQuoteOut(BaseModel):
    base_product: Optional[str]
    base_sku: Optional[str]
    base_size: Optional[str]
    changes: list[str]
    est_price: Optional[float]
    breakdown: list[PriceBreakdownItemOut]
    ai_used: bool
    model: Optional[str]
    error: Optional[str]


@router.post("/ai-quote", response_model=AiQuoteOut)
async def ai_quote(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    data = await image.read()
    mime = image.content_type or "image/jpeg"
    result = customization_ai_service.ai_quote(db, data, mime)
    return AiQuoteOut(
        base_product=result.base_product,
        base_sku=result.base_sku,
        base_size=result.base_size,
        changes=result.changes,
        est_price=result.est_price,
        breakdown=[PriceBreakdownItemOut(**b.__dict__) for b in result.breakdown],
        ai_used=result.ai_used,
        model=result.model,
        error=result.error,
    )


@router.post("/confirm", response_model=ConfirmOut, status_code=201)
def confirm(payload: ConfirmIn, db: Session = Depends(get_db)):
    try:
        r = customization_service.confirm(
            db,
            base_sku_code=payload.base_sku_code,
            dimension_changes=payload.dimension_changes,
            order_no=payload.order_no,
            note=payload.note,
            qty_overrides=payload.qty_overrides,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return ConfirmOut(**r.__dict__)

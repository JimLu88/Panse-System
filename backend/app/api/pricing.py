"""定价总表读取 API.

只读端点: 前端定价表页面展示导入的 PricingSku (四档售价 + 成本拆分)。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.pricing import PricingSku

router = APIRouter(prefix="/api/pricing-skus", tags=["pricing"])


class PricingSkuOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_code: str
    sku: Optional[str]
    sku_code: str
    size_category: Optional[str]
    list_price: Optional[Decimal]
    daily_price: Optional[Decimal]
    small_promo: Optional[Decimal]
    mid_promo: Optional[Decimal]
    big_promo: Optional[Decimal]
    big_promo_margin: Optional[Decimal]
    gross_margin_rate: Optional[Decimal]
    accounting_cost: Optional[Decimal]
    physical_cost: Optional[Decimal]
    platform_fee_rate: Optional[Decimal]
    tax: Optional[Decimal]


class PricingSkuListOut(BaseModel):
    total: int
    items: list[PricingSkuOut]


@router.get("", response_model=PricingSkuListOut)
def list_pricing_skus(
    q: Optional[str] = Query(None, description="按 product_code / sku_code / sku 模糊搜"),
    size_category: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(PricingSku)
    count_stmt = select(func.count(PricingSku.id))
    if q:
        like = f"%{q.strip()}%"
        cond = or_(
            PricingSku.product_code.ilike(like),
            PricingSku.sku_code.ilike(like),
            PricingSku.sku.ilike(like),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if size_category:
        stmt = stmt.where(PricingSku.size_category == size_category)
        count_stmt = count_stmt.where(PricingSku.size_category == size_category)
    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(PricingSku.product_code, PricingSku.sku_code).limit(limit).offset(offset)
    ).scalars().all()
    return PricingSkuListOut(
        total=total,
        items=[PricingSkuOut.model_validate(r) for r in rows],
    )

"""淘宝商品导出对应表 API (Task 5)."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.taobao_listing import TaobaoListing
from app.services import taobao_listing_service

router = APIRouter(prefix="/api/taobao-listings", tags=["taobao"])


class TaobaoListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    taobao_item_id: str
    taobao_sku_id: Optional[str]
    title: Optional[str]
    merchant_code: Optional[str]
    sku_spec: Optional[str]
    category_name: Optional[str]
    list_price: Optional[Decimal]
    sku_price: Optional[Decimal]
    stock: Optional[int]
    sku_code: Optional[str]
    product_code: Optional[str]
    matched: bool


class TaobaoListingListOut(BaseModel):
    total: int
    matched: int
    items: list[TaobaoListingOut]


@router.post("/import")
async def import_taobao_export(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """上传淘宝商品导出 Excel, 解析入库并按商家编码自动匹配系统 SKU."""
    name = (file.filename or "").lower()
    if not (name.endswith(".xlsx") or name.endswith(".xls")):
        raise HTTPException(400, "请上传 .xlsx / .xls 文件")
    content = await file.read()
    try:
        result = taobao_listing_service.import_listings(db, content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"解析失败: {e}")
    return result


@router.get("", response_model=TaobaoListingListOut)
def list_taobao_listings(
    q: Optional[str] = Query(None, description="搜商品ID/skuId/商家编码/标题"),
    matched: Optional[bool] = Query(None, description="仅看已匹配/未匹配"),
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    base = select(TaobaoListing)
    if q:
        like = f"%{q.strip()}%"
        base = base.where(
            or_(
                TaobaoListing.taobao_item_id.ilike(like),
                TaobaoListing.taobao_sku_id.ilike(like),
                TaobaoListing.merchant_code.ilike(like),
                TaobaoListing.title.ilike(like),
                TaobaoListing.sku_code.ilike(like),
            )
        )
    if matched is not None:
        base = base.where(TaobaoListing.matched == matched)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    matched_count = db.scalar(
        select(func.count()).select_from(
            base.where(TaobaoListing.matched == True).subquery()  # noqa: E712
        )
    ) or 0
    rows = db.execute(
        base.order_by(TaobaoListing.taobao_item_id, TaobaoListing.id)
        .limit(limit)
        .offset(offset)
    ).scalars().all()
    return TaobaoListingListOut(
        total=total,
        matched=matched_count,
        items=[TaobaoListingOut.model_validate(r) for r in rows],
    )


class TaobaoListingPatch(BaseModel):
    sku_code: Optional[str] = None
    product_code: Optional[str] = None


@router.patch("/{listing_id}", response_model=TaobaoListingOut)
def update_taobao_listing(
    listing_id: int,
    body: TaobaoListingPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """人工修正某条对应关系 (改系统 sku_code / product_code)."""
    row = db.get(TaobaoListing, listing_id)
    if not row:
        raise HTTPException(404, "not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    row.matched = bool(row.sku_code or row.product_code)
    db.commit()
    db.refresh(row)
    return TaobaoListingOut.model_validate(row)

"""评价资产台账 API (Plan1 v2): /api/review-assets。

权限 key = review-assets (page_permissions 全局 enforce); 写设置 require_role(admin/operator)。
业务逻辑在 review_asset_service; 导入照抄 finance refill-xlsx (openpyxl + import_storage 归档)。
"""
from __future__ import annotations

import io
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.review_asset import ReviewAsset
from app.services import import_storage, review_asset_service as svc

router = APIRouter(prefix="/api/review-assets", tags=["review-assets"])


class ReviewAssetCreate(BaseModel):
    order_no: str
    review_date: Optional[date] = None
    image_count: int = 0
    rating: Optional[int] = None
    review_text: Optional[str] = None
    product_code: Optional[str] = None
    sku_name: Optional[str] = None
    shop: Optional[str] = None
    source: Optional[str] = None
    remark: Optional[str] = None


class ReviewAssetPatch(BaseModel):
    review_date: Optional[date] = None
    image_count: Optional[int] = None
    rating: Optional[int] = None
    review_text: Optional[str] = None
    product_code: Optional[str] = None
    sku_name: Optional[str] = None
    shop: Optional[str] = None
    status: Optional[str] = None
    remark: Optional[str] = None


class SettingsPayload(BaseModel):
    fold_days: Optional[int] = None
    pending_timeout_days: Optional[int] = None
    coverage_min: Optional[int] = None


def _get_or_404(db: Session, asset_id: int) -> ReviewAsset:
    ra = db.get(ReviewAsset, asset_id)
    if not ra:
        raise HTTPException(404, "评价资产不存在")
    return ra


# ---- 静态路径 (须在 /{asset_id} 前注册) ----

@router.get("")
def list_review_assets(
    status: Optional[str] = None,
    product_code: Optional[str] = None,
    shop: Optional[str] = None,
    source: Optional[str] = None,
    due_in_days: Optional[int] = Query(None, description="临近折叠: fold_due_date 在 N 天内"),
    keyword: Optional[str] = Query(None, description="订单号/产品/SKU 模糊"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    rows = svc.list_assets(
        db, status=status, product_code=product_code, shop=shop, source=source,
        due_in_days=due_in_days, keyword=keyword, limit=limit, offset=offset,
    )
    return {"items": [svc.to_dict(r) for r in rows]}


@router.get("/stats")
def review_stats(db: Session = Depends(get_db)):
    return svc.stats(db)


@router.get("/coverage")
def review_coverage(db: Session = Depends(get_db)):
    return {"items": svc.coverage(db)}


@router.get("/settings")
def get_review_settings(db: Session = Depends(get_db)):
    return svc.get_settings(db)


@router.put("/settings")
def put_review_settings(
    payload: SettingsPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    try:
        out = svc.update_settings(
            db, fold_days=payload.fold_days,
            pending_timeout_days=payload.pending_timeout_days,
            coverage_min=payload.coverage_min,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return out


@router.get("/import/template")
def download_template():
    data = svc.build_template_xlsx()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=review_assets_template.xlsx"},
    )


@router.post("/import")
async def import_review_assets(file: UploadFile = File(...), db: Session = Depends(get_db)):
    import openpyxl

    data = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        raise HTTPException(400, "无法解析 xlsx 文件")
    arch = import_storage.archive(
        db, content=data, original_name=file.filename or "评价资产.xlsx",
        kind="review", source="web",
    )
    rep = svc.import_rows(db, wb)
    import_storage.update_summary(db, arch.file.id, {
        "inserted": rep.inserted, "skipped_duplicate": rep.skipped_duplicate,
        "skipped_invalid": rep.skipped_invalid, "unlinked": rep.unlinked,
    })
    db.commit()
    return {
        "inserted": rep.inserted, "skipped_duplicate": rep.skipped_duplicate,
        "skipped_invalid": rep.skipped_invalid, "unlinked": rep.unlinked,
        "archived": not arch.is_duplicate,
    }


@router.post("/from-order/{order_id}")
def create_from_order(order_id: int, db: Session = Depends(get_db)):
    try:
        ra, created = svc.from_order(db, order_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"created": created, "asset": svc.to_dict(ra)}


@router.post("")
def create_review_asset(payload: ReviewAssetCreate, db: Session = Depends(get_db)):
    try:
        ra = svc.create_manual(
            db, order_no=payload.order_no, review_date=payload.review_date,
            image_count=payload.image_count, rating=payload.rating,
            review_text=payload.review_text, product_code=payload.product_code,
            sku_name=payload.sku_name, shop=payload.shop, source=payload.source,
            remark=payload.remark,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return svc.to_dict(ra)


# ---- 动态 /{asset_id} ----

@router.patch("/{asset_id}")
def patch_review_asset(asset_id: int, payload: ReviewAssetPatch, db: Session = Depends(get_db)):
    ra = _get_or_404(db, asset_id)
    patch = payload.model_dump(exclude_unset=True)
    try:
        svc.update_asset(db, ra, patch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return svc.to_dict(ra)


@router.delete("/{asset_id}")
def delete_review_asset(asset_id: int, db: Session = Depends(get_db)):
    ra = _get_or_404(db, asset_id)
    db.delete(ra)
    db.commit()
    return {"ok": True}

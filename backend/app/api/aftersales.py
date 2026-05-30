"""售后 / 退货 API (Phase 5, 业务需求 9)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.marketing import AfterSales
from app.services import bill_import_service, return_service

router = APIRouter(prefix="/api/aftersales", tags=["aftersales"])


class AfterSalesOut(BaseModel):
    id: int
    platform_order_no: str
    status: Optional[str]
    reason: Optional[str]
    refill_tracking_no: Optional[str]
    second_inbound_confirmed: Optional[str]
    processed_at: Optional[str]
    remark: Optional[str]


def _out(a: AfterSales) -> AfterSalesOut:
    return AfterSalesOut(
        id=a.id, platform_order_no=a.platform_order_no, status=a.status,
        reason=a.reason, refill_tracking_no=a.refill_tracking_no,
        second_inbound_confirmed=a.second_inbound_confirmed,
        processed_at=a.processed_at.isoformat() if a.processed_at else None,
        remark=a.remark,
    )


@router.get("", response_model=list[AfterSalesOut])
def list_aftersales(
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    q = select(AfterSales).order_by(AfterSales.id.desc()).limit(limit)
    if status:
        q = q.where(AfterSales.status == status)
    return [_out(a) for a in db.execute(q).scalars()]


class CreateReturnIn(BaseModel):
    order_no: str
    reason: str
    tracking_no: Optional[str] = None


@router.post("", response_model=AfterSalesOut, status_code=201)
def create_return(
    payload: CreateReturnIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        a = return_service.create_return(
            db, order_no=payload.order_no, reason=payload.reason,
            tracking_no=payload.tracking_no,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _out(a)


@router.post("/{after_sales_id}/mark-received", response_model=AfterSalesOut)
def mark_received(
    after_sales_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        a = return_service.mark_received(db, after_sales_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.commit()
    return _out(a)


class ConfirmInboundIn(BaseModel):
    product_code: str
    sku_code: Optional[str] = None
    qty: int = 1


@router.post("/{after_sales_id}/confirm-inbound", response_model=AfterSalesOut)
def confirm_inbound(
    after_sales_id: int, payload: ConfirmInboundIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """业务需求 9: 二次确认入库 (整产品, 不拆 BOM)."""
    try:
        a = return_service.confirm_return_inbound(
            db, after_sales_id, product_code=payload.product_code,
            sku_code=payload.sku_code, qty=payload.qty,
            actor=getattr(user, "username", "user"),
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.commit()
    return _out(a)


class MarkDamagedIn(BaseModel):
    reason: str = "产品损坏不入库"


@router.post("/{after_sales_id}/mark-damaged", response_model=AfterSalesOut)
def mark_damaged(
    after_sales_id: int, payload: MarkDamagedIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    try:
        a = return_service.mark_return_damaged(
            db, after_sales_id, reason=payload.reason,
            actor=getattr(user, "username", "user"),
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.commit()
    return _out(a)


class DisassembleIn(BaseModel):
    product_code: str
    sku_code: Optional[str] = None
    qty: int


@router.post("/disassemble-product")
def disassemble(
    payload: DisassembleIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """业务需求 9: 用户手动点 "拆 BOM" 把成品拆成物料."""
    from app.services import inventory_lock_service
    try:
        result = inventory_lock_service.disassemble_product_to_parts(
            db, product_code=payload.product_code, sku_code=payload.sku_code,
            qty=payload.qty, actor=getattr(user, "username", "user"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result


# -------- 批量 CSV 导入 --------

class AfterSalesImportResult(BaseModel):
    inserted: int
    skipped_invalid: int
    errors: list[str]


@router.post("/import-csv", response_model=AfterSalesImportResult)
async def import_aftersales_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """批量导入售后表 CSV (订单号必填; 其余字段按列名自动映射)。"""
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    r = bill_import_service.import_aftersales_csv(db, text)
    db.commit()
    return AfterSalesImportResult(inserted=r.inserted, skipped_invalid=r.skipped_invalid, errors=r.errors)

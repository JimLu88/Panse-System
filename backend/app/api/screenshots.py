"""截图自动化 API (Phase 3, 业务需求 1/6).

POST /api/screenshots/qianniu-orders/parse   上传千牛订单截图 → 预览 JSON
POST /api/screenshots/qianniu-orders/commit  把预览 JSON 确认入库 (创建 Orders)
POST /api/screenshots/purchase/parse         上传采购单截图 → 预览
POST /api/screenshots/purchase/commit        确认入库 PartPurchase
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.finance import FactoryReconciliation
from app.models.order import Order, PartPurchase
from app.rate_limit import limiter
from app.services import vision_ocr_service
from app.services.ai_provider import AiUnavailable

router = APIRouter(prefix="/api/screenshots", tags=["screenshots"])

_MAX_BYTES = 20 * 1024 * 1024   # 20 MB / 张, 防止 OOM


def _read_image(file: UploadFile, content: bytes) -> tuple[bytes, str]:
    if not content:
        raise HTTPException(400, "空文件")
    if len(content) > _MAX_BYTES:
        raise HTTPException(413, "图片过大 (>20MB)")
    mime = file.content_type or "image/jpeg"
    if not mime.startswith("image/"):
        raise HTTPException(400, f"非图片类型: {mime}")
    return content, mime


# ----------------------------- 千牛订单 -------------------------- #


@router.post("/qianniu-orders/parse")
@limiter.limit("10/minute")
async def parse_qianniu(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """业务需求 1: 千牛截图 → AI 解析订单字段, 不入库."""
    content = await file.read()
    img, mime = _read_image(file, content)
    try:
        data = await asyncio.to_thread(vision_ocr_service.parse_qianniu_order, db, img, mime=mime)
    except AiUnavailable as e:
        raise HTTPException(503, str(e))
    return {
        "image_b64": base64.b64encode(img).decode("ascii"),
        "mime": mime,
        **data,
    }


class CommitQianniuOrderIn(BaseModel):
    order_no: str
    platform: Optional[str] = "淘宝"
    order_date: Optional[str] = None  # YYYY-MM-DD
    pay_time: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    sku: Optional[str] = None
    sku_code: Optional[str] = None
    qty: int = 1
    paid_amount: Optional[float] = None
    discount: Optional[float] = None
    platform_fee: Optional[float] = None
    remark: Optional[str] = None


class CommitQianniuIn(BaseModel):
    orders: list[CommitQianniuOrderIn]


@router.post("/qianniu-orders/commit")
def commit_qianniu(
    payload: CommitQianniuIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """业务需求 1: 用户确认 AI 解析后, 批量入 Orders 表."""
    inserted = 0
    skipped: list[str] = []
    for o in payload.orders:
        if not o.order_no:
            continue
        # 去重
        existing = db.execute(
            select(Order).where(Order.order_no == o.order_no)
        ).scalar_one_or_none()
        if existing:
            skipped.append(o.order_no)
            continue
        order_date = None
        if o.order_date:
            try:
                order_date = datetime.strptime(o.order_date, "%Y-%m-%d").date()
            except ValueError:
                pass
        order = Order(
            platform=o.platform or "淘宝",
            order_no=o.order_no,
            order_date=order_date,
            customer_name=o.customer_name,
            customer_phone=o.customer_phone,
            customer_address=o.customer_address,
            product_code=o.product_code,
            product_name=o.product_name,
            sku=o.sku,
            sku_code=o.sku_code,
            qty=o.qty or 1,
            paid_amount=Decimal(str(o.paid_amount)) if o.paid_amount is not None else None,
            remark=o.remark,
            status="pending_payment",
        )
        db.add(order)
        inserted += 1
    db.commit()
    return {"inserted": inserted, "skipped_existing": skipped}


# ----------------------------- 采购单 ---------------------------- #


@router.post("/purchase/parse")
@limiter.limit("10/minute")
async def parse_purchase(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    content = await file.read()
    img, mime = _read_image(file, content)
    try:
        data = await asyncio.to_thread(vision_ocr_service.parse_purchase_invoice, db, img, mime=mime)
    except AiUnavailable as e:
        raise HTTPException(503, str(e))
    return {
        "image_b64": base64.b64encode(img).decode("ascii"),
        "mime": mime,
        **data,
    }


class PurchaseLineIn(BaseModel):
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    spec: Optional[str] = None
    qty: float = 1
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class CommitPurchaseIn(BaseModel):
    supplier: Optional[str] = None
    purchase_date: Optional[str] = None
    purchase_no: Optional[str] = None
    tracking_no: Optional[str] = None
    carrier: Optional[str] = None
    freight: Optional[float] = None
    total_amount: Optional[float] = None
    remark: Optional[str] = None
    lines: list[PurchaseLineIn]


@router.post("/purchase/commit")
def commit_purchase(
    payload: CommitPurchaseIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """业务需求 6: 采购单截图 → 入库 PartPurchase. 一张单按行展开成多条 PartPurchase."""
    pdate = None
    if payload.purchase_date:
        try:
            pdate = datetime.strptime(payload.purchase_date, "%Y-%m-%d").date()
        except ValueError:
            pass
    base_no = payload.purchase_no or f"P{int(datetime.now().timestamp())}"
    inserted = 0
    for idx, line in enumerate(payload.lines, start=1):
        if not (line.material_code or line.material_name):
            continue
        try:
            qty = Decimal(str(line.qty)) if line.qty else Decimal("1")
        except (InvalidOperation, ValueError):
            qty = Decimal("1")
        amount = Decimal(str(line.amount)) if line.amount is not None else None
        unit_price = Decimal(str(line.unit_price)) if line.unit_price is not None else None
        if amount is None and unit_price is not None:
            amount = (unit_price * qty).quantize(Decimal("0.01"))
        pp = PartPurchase(
            purchase_no=f"{base_no}_{idx}" if len(payload.lines) > 1 else base_no,
            supplier=payload.supplier,
            purchase_date=pdate,
            material_code=line.material_code,
            material_name=line.material_name,
            spec=line.spec,
            qty=qty,
            unit_price=unit_price,
            amount=amount,
            tracking_no=payload.tracking_no,
            freight=Decimal(str(payload.freight)) if payload.freight else None,
            total_amount=Decimal(str(payload.total_amount)) if payload.total_amount else None,
        )
        db.add(pp)
        inserted += 1
    db.commit()
    return {"inserted": inserted, "purchase_no": base_no, "has_tracking": bool(payload.tracking_no)}


# --------------------------- 工厂对账单 (Task 3) --------------------------- #


@router.post("/factory-recon/parse")
@limiter.limit("10/minute")
async def parse_factory_recon(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """工厂对账单截图 → AI 解析每行对账记录, 不入库 (表格类对账请用 Excel 导入)."""
    content = await file.read()
    img, mime = _read_image(file, content)
    try:
        data = await asyncio.to_thread(
            vision_ocr_service.parse_factory_reconciliation, db, img, mime=mime
        )
    except AiUnavailable as e:
        raise HTTPException(503, str(e))
    return {
        "image_b64": base64.b64encode(img).decode("ascii"),
        "mime": mime,
        **data,
    }


class FactoryReconRowIn(BaseModel):
    factory_name: str
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    order_amount: Optional[float] = None
    bill_amount: Optional[float] = None
    paid_amount: Optional[float] = None
    alipay_flow_no: Optional[str] = None
    remark: Optional[str] = None


class CommitFactoryReconIn(BaseModel):
    rows: list[FactoryReconRowIn]


def _parse_date(s: Optional[str]):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _dec(v: Optional[float]) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


@router.post("/factory-recon/commit")
def commit_factory_recon(
    payload: CommitFactoryReconIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """确认 AI 解析后批量入库 FactoryReconciliation; diff_amount 自动算 (账单-已付)."""
    inserted = 0
    skipped: list[str] = []
    for row in payload.rows:
        if not row.factory_name:
            continue
        bill = _dec(row.bill_amount)
        paid = _dec(row.paid_amount)
        diff = (bill - paid) if (bill is not None and paid is not None) else Decimal("0")
        rec = FactoryReconciliation(
            factory_name=row.factory_name,
            period_start=_parse_date(row.period_start),
            period_end=_parse_date(row.period_end),
            order_amount=_dec(row.order_amount),
            bill_amount=bill,
            paid_amount=paid,
            diff_amount=diff,
            alipay_flow_no=row.alipay_flow_no,
            remark=row.remark,
            status="open",
        )
        db.add(rec)
        inserted += 1
    db.commit()
    return {"inserted": inserted, "skipped": skipped}

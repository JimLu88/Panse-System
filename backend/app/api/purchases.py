"""配件采购 API (Excel 表 7 → 业务需求: OCR 拍照入 + 历史发票留存可查看).

POST /api/purchases/upload-ocr        上传发票图 → OCR → 入库 (拍照自动入)
GET  /api/purchases                   列出采购记录
GET  /api/purchases/{id}/source-image 取发票原图 (带权限)
GET  /api/purchases/files             按年月列出已上传发票文件
GET  /api/purchases/files/{id}/image  取某发票文件原图
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.order import PartPurchase, PurchaseFile
from app.services import ocr_service, purchase_storage

router = APIRouter(prefix="/api/purchases", tags=["purchases"])

_MAX_UPLOAD = 15 * 1024 * 1024   # 15 MB


class PurchaseLineOut(BaseModel):
    item_name: str
    spec: str
    unit: str
    qty: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]


class PurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    purchase_no: str
    supplier: Optional[str]
    purchase_date: Optional[date]
    material_code: Optional[str]
    material_name: Optional[str]
    spec: Optional[str]
    qty: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    tracking_no: Optional[str]
    freight: Optional[Decimal]
    total_amount: Optional[Decimal]
    payment_status: str
    source_file_id: Optional[int]
    ocr_warnings: list[str] = []
    ocr_model: Optional[str]


class UploadOcrResult(BaseModel):
    file_id: int
    supplier: Optional[str]
    purchase_date: Optional[date]
    tracking_no: Optional[str]
    freight: Optional[Decimal]
    total_amount: Optional[Decimal]
    confidence: float
    warnings: list[str]
    lines: list[PurchaseLineOut]
    created_purchase_ids: list[int]


def _next_purchase_no(db: Session) -> str:
    """生成采购单号: PUR{YYYYMMDD}{NN}."""
    today = date.today()
    prefix = f"PUR{today:%Y%m%d}"
    last = db.execute(
        select(PartPurchase.purchase_no).where(PartPurchase.purchase_no.like(f"{prefix}%"))
        .order_by(PartPurchase.purchase_no.desc()).limit(1)
    ).scalar_one_or_none()
    seq = 1
    if last:
        try:
            seq = int(last[len(prefix):]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"{prefix}{seq:02d}"


@router.post("/upload-ocr", response_model=UploadOcrResult)
async def upload_and_ocr(
    file: UploadFile = File(...),
    auto_commit: bool = Query(True, description="OCR 后是否直接入库 (每行明细一条采购记录)"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """上传配件采购发票图 → OCR 识别 → (可选) 入库. 原图永久留存可回看。"""
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    if len(content) > _MAX_UPLOAD:
        raise HTTPException(413, "文件过大 (上限 15MB)")

    saved = purchase_storage.save_upload(
        content=content, original_name=file.filename or "invoice.jpg",
    )
    pf = PurchaseFile(
        year=saved["year"], month=saved["month"], file_path=saved["file_path"],
        original_name=saved["original_name"], mime_type=saved["mime_type"],
        size_bytes=saved["size_bytes"], uploaded_by=user.username,
    )
    db.add(pf)
    db.flush()

    warnings: list[str] = []
    parsed = None
    try:
        parsed = ocr_service.ocr_purchase_invoice(
            db, image_bytes=content, mime=saved["mime_type"] or "image/jpeg",
        )
    except ocr_service.OcrUnavailable as e:
        warnings = [f"OCR 调用失败: {e}"]
    except ocr_service.OcrParseError as e:
        warnings = [f"OCR 返回无法解析: {e}"]

    created_ids: list[int] = []
    lines_out: list[PurchaseLineOut] = []
    if parsed is not None:
        warnings = parsed.warnings
        lines_out = [
            PurchaseLineOut(
                item_name=ln.item_name, spec=ln.spec, unit=ln.unit,
                qty=ln.qty, unit_price=ln.unit_price, amount=ln.amount,
            )
            for ln in parsed.lines
        ]
        if auto_commit and parsed.lines:
            for ln in parsed.lines:
                pp = PartPurchase(
                    purchase_no=_next_purchase_no(db),
                    supplier=parsed.supplier,
                    purchase_date=parsed.purchase_date,
                    material_name=ln.item_name,
                    spec=ln.spec,
                    qty=ln.qty or Decimal("1"),
                    unit_price=ln.unit_price,
                    amount=ln.amount,
                    tracking_no=parsed.tracking_no,
                    freight=parsed.freight,
                    total_amount=parsed.total_amount,
                    source_file_id=pf.id,
                    ocr_warnings=warnings,
                    ocr_model=parsed.model,
                )
                db.add(pp)
                db.flush()
                created_ids.append(pp.id)

    db.commit()
    return UploadOcrResult(
        file_id=pf.id,
        supplier=parsed.supplier if parsed else None,
        purchase_date=parsed.purchase_date if parsed else None,
        tracking_no=parsed.tracking_no if parsed else None,
        freight=parsed.freight if parsed else None,
        total_amount=parsed.total_amount if parsed else None,
        confidence=float(parsed.confidence) if parsed else 0.0,
        warnings=warnings,
        lines=lines_out,
        created_purchase_ids=created_ids,
    )


@router.get("", response_model=list[PurchaseOut])
def list_purchases(
    limit: int = Query(200, le=1000),
    supplier: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = select(PartPurchase)
    if supplier:
        q = q.where(PartPurchase.supplier == supplier)
    rows = db.execute(
        q.order_by(PartPurchase.purchase_date.desc().nulls_last(), PartPurchase.id.desc())
        .limit(limit)
    ).scalars().all()
    out = []
    for p in rows:
        out.append(PurchaseOut(
            id=p.id, purchase_no=p.purchase_no, supplier=p.supplier,
            purchase_date=p.purchase_date, material_code=p.material_code,
            material_name=p.material_name, spec=p.spec, qty=p.qty,
            unit_price=p.unit_price, amount=p.amount, tracking_no=p.tracking_no,
            freight=p.freight, total_amount=p.total_amount,
            payment_status=p.payment_status, source_file_id=p.source_file_id,
            ocr_warnings=p.ocr_warnings or [], ocr_model=p.ocr_model,
        ))
    return out


@router.get("/{purchase_id}/source-image")
def get_source_image(purchase_id: int, db: Session = Depends(get_db)):
    """取某条采购记录关联的发票原图."""
    p = db.get(PartPurchase, purchase_id)
    if p is None or p.source_file_id is None:
        raise HTTPException(404, "无关联发票图")
    pf = db.get(PurchaseFile, p.source_file_id)
    if pf is None:
        raise HTTPException(404, "发票文件不存在")
    try:
        data = purchase_storage.read(pf.file_path)
    except (FileNotFoundError, PermissionError) as e:
        raise HTTPException(404, str(e))
    return Response(content=data, media_type=pf.mime_type or "application/octet-stream")


class PurchaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    year: int
    month: int
    original_name: Optional[str]
    mime_type: Optional[str]
    size_bytes: Optional[int]
    uploaded_by: Optional[str]


@router.get("/files", response_model=list[PurchaseFileOut])
def list_files(
    year: Optional[int] = None,
    month: Optional[int] = None,
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    """按年月列出已上传的发票文件 (历史发票留存)."""
    q = select(PurchaseFile)
    if year:
        q = q.where(PurchaseFile.year == year)
    if month:
        q = q.where(PurchaseFile.month == month)
    rows = db.execute(
        q.order_by(PurchaseFile.year.desc(), PurchaseFile.month.desc(), PurchaseFile.id.desc())
        .limit(limit)
    ).scalars().all()
    return rows


@router.get("/files/{file_id}/image")
def get_file_image(file_id: int, db: Session = Depends(get_db)):
    pf = db.get(PurchaseFile, file_id)
    if pf is None:
        raise HTTPException(404, "发票文件不存在")
    try:
        data = purchase_storage.read(pf.file_path)
    except (FileNotFoundError, PermissionError) as e:
        raise HTTPException(404, str(e))
    return Response(content=data, media_type=pf.mime_type or "application/octet-stream")

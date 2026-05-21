"""供应商对账 API (业务需求扩展).

/api/suppliers                 GET/POST/PATCH 供应商 CRUD (admin/operator)
/api/suppliers/{id}/delivery-notes
        GET 列表 (按 month 筛选)
        POST upload — 文件上传 + OCR → 入库 (业务需求: 拍照自动入)
/api/delivery-notes/{id}       GET 详情 (含行 + 候选 + 警告)
/api/delivery-notes/{id}       PATCH 改 status / 备注
/api/delivery-notes/{id}/lines/{line_id}/match  PATCH 用户手动改匹配
/api/delivery-notes/{id}/rematch                POST 重跑匹配
/api/delivery-notes/{id}/source-image           GET 原图 (带权限)
/api/suppliers/{id}/delivery-files-folder       GET 文件夹列表 (业务需求: UI 按钮可点进入)
/api/suppliers/{id}/statements/{year}/{month}.xlsx   下载 Excel
/api/suppliers/{id}/statements/{year}/{month}.html   打印 HTML → PDF
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.supplier import (
    DeliveryFile,
    DeliveryNote,
    DeliveryNoteLine,
    Supplier,
)
from app.services import delivery_matcher, delivery_storage, ocr_service, statement_service

router = APIRouter(prefix="/api", tags=["suppliers"])


# ----------------------------- Schemas --------------------------------- #


class SupplierOut(BaseModel):
    id: int
    name: str
    supplier_type: str
    contact: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    is_active: bool
    remark: Optional[str] = None


class SupplierIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    supplier_type: str = "other"
    contact: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    remark: Optional[str] = None


class SupplierPatch(BaseModel):
    name: Optional[str] = None
    supplier_type: Optional[str] = None
    contact: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    is_active: Optional[bool] = None
    remark: Optional[str] = None


class DeliveryLineOut(BaseModel):
    id: int
    line_no: int
    item_name: Optional[str]
    spec: Optional[str]
    unit: Optional[str]
    qty: float
    unit_price: Optional[float]
    amount: Optional[float]
    matched_order_no: Optional[str]
    match_confidence: Optional[float]
    match_method: Optional[str]
    match_candidates: list[dict] = []
    ocr_warnings: list[str] = []
    remark: Optional[str] = None


class DeliveryNoteOut(BaseModel):
    id: int
    supplier_id: int
    supplier_name: str
    note_no: Optional[str]
    delivery_date: Optional[str]
    total_amount: Optional[float]
    status: str
    ocr_confidence: Optional[float]
    ocr_warnings: list[str] = []
    ocr_model: Optional[str]
    source_file_id: Optional[int]
    remark: Optional[str] = None
    lines: list[DeliveryLineOut] = []


class DeliveryNoteUpdate(BaseModel):
    status: Optional[str] = None
    note_no: Optional[str] = None
    delivery_date: Optional[str] = None
    total_amount: Optional[float] = None
    remark: Optional[str] = None
    alipay_flow_no: Optional[str] = None


class LineMatchPatch(BaseModel):
    """用户从下拉里选一个候选 — 后端原样落盘 + 标 confidence=100 + method=manual."""
    matched_order_no: Optional[str] = None
    match_confidence: Optional[float] = None  # None = 自动定 100


# ----------------------------- Suppliers CRUD -------------------------- #


def _supplier_out(s: Supplier) -> SupplierOut:
    return SupplierOut(
        id=s.id, name=s.name, supplier_type=s.supplier_type,
        contact=s.contact, phone=s.phone, address=s.address,
        payment_terms=s.payment_terms, is_active=s.is_active, remark=s.remark,
    )


@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(
    active_only: bool = Query(default=True),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Supplier).order_by(Supplier.id)
    if active_only:
        q = q.where(Supplier.is_active.is_(True))
    rows = db.execute(q).scalars().all()
    return [_supplier_out(s) for s in rows]


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(
    payload: SupplierIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    if db.execute(select(Supplier.id).where(Supplier.name == payload.name)).first():
        raise HTTPException(409, f"供应商名 {payload.name!r} 已存在")
    s = Supplier(**payload.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return _supplier_out(s)


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
def patch_supplier(
    supplier_id: int,
    payload: SupplierPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    s = db.get(Supplier, supplier_id)
    if s is None:
        raise HTTPException(404, "供应商不存在")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _supplier_out(s)


# ----------------------------- Delivery Notes -------------------------- #


def _line_out(ln: DeliveryNoteLine) -> DeliveryLineOut:
    return DeliveryLineOut(
        id=ln.id, line_no=ln.line_no,
        item_name=ln.item_name, spec=ln.spec, unit=ln.unit,
        qty=float(ln.qty),
        unit_price=float(ln.unit_price) if ln.unit_price is not None else None,
        amount=float(ln.amount) if ln.amount is not None else None,
        matched_order_no=ln.matched_order_no,
        match_confidence=float(ln.match_confidence) if ln.match_confidence is not None else None,
        match_method=ln.match_method,
        match_candidates=ln.match_candidates or [],
        ocr_warnings=ln.ocr_warnings or [],
        remark=ln.remark,
    )


def _note_out(db: Session, n: DeliveryNote, *, with_lines: bool = True) -> DeliveryNoteOut:
    s = db.get(Supplier, n.supplier_id)
    lines = []
    if with_lines:
        lines = [_line_out(ln) for ln in db.execute(
            select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id == n.id)
            .order_by(DeliveryNoteLine.line_no)
        ).scalars().all()]
    return DeliveryNoteOut(
        id=n.id, supplier_id=n.supplier_id, supplier_name=s.name if s else "",
        note_no=n.note_no,
        delivery_date=n.delivery_date.isoformat() if n.delivery_date else None,
        total_amount=float(n.total_amount) if n.total_amount is not None else None,
        status=n.status,
        ocr_confidence=float(n.ocr_confidence) if n.ocr_confidence is not None else None,
        ocr_warnings=n.ocr_warnings or [],
        ocr_model=n.ocr_model,
        source_file_id=n.source_file_id,
        remark=n.remark,
        lines=lines,
    )


@router.get("/suppliers/{supplier_id}/delivery-notes", response_model=list[DeliveryNoteOut])
def list_notes(
    supplier_id: int,
    year: Optional[int] = None,
    month: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(DeliveryNote).where(DeliveryNote.supplier_id == supplier_id)
    if year is not None and month is not None:
        from datetime import date as _d
        start = _d(year, month, 1)
        end = _d(year + 1, 1, 1) if month == 12 else _d(year, month + 1, 1)
        q = q.where(and_(
            DeliveryNote.delivery_date >= start, DeliveryNote.delivery_date < end,
        ))
    if status:
        q = q.where(DeliveryNote.status == status)
    rows = db.execute(q.order_by(DeliveryNote.delivery_date.desc(), DeliveryNote.id.desc())
                       .limit(500)).scalars().all()
    return [_note_out(db, n, with_lines=False) for n in rows]


@router.post("/suppliers/{supplier_id}/delivery-notes", response_model=DeliveryNoteOut, status_code=201)
async def upload_and_ocr(
    supplier_id: int,
    file: UploadFile = File(...),
    on_date: Optional[str] = Form(default=None),  # YYYY-MM-DD; 不传 = 今天
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """业务需求: 拍照上传 → OCR 自动入。失败 / 警告由前端弹窗。"""
    s = db.get(Supplier, supplier_id)
    if s is None:
        raise HTTPException(404, "供应商不存在")

    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "文件超过 20MB")

    # 1) 落盘
    from datetime import date as _d
    archive_date = None
    if on_date:
        try:
            archive_date = _d.fromisoformat(on_date)
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
    saved = delivery_storage.save_upload(
        supplier_id, content=content,
        original_name=file.filename or "upload.jpg",
        on_date=archive_date,
    )
    df = DeliveryFile(
        supplier_id=supplier_id,
        year=saved["year"], month=saved["month"],
        file_path=saved["file_path"],
        original_name=saved["original_name"],
        mime_type=saved["mime_type"],
        size_bytes=saved["size_bytes"],
        uploaded_by=user.username,
    )
    db.add(df)
    db.flush()

    # 2) OCR
    try:
        parsed = ocr_service.ocr_delivery_note(
            db, image_bytes=content, mime=saved["mime_type"] or "image/jpeg",
            supplier_name=s.name, supplier_type=s.supplier_type,
        )
    except ocr_service.OcrUnavailable as e:
        # 入了文件, 但 OCR 没配 / 失败 — 仍记一条 pending_review 空单, 让用户事后再跑
        note = DeliveryNote(
            supplier_id=supplier_id, source_file_id=df.id,
            status="pending_review", ocr_warnings=[f"OCR 调用失败: {e}"],
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return _note_out(db, note)
    except ocr_service.OcrParseError as e:
        note = DeliveryNote(
            supplier_id=supplier_id, source_file_id=df.id,
            status="pending_review",
            ocr_warnings=[f"OCR 返回无法解析: {e}"],
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return _note_out(db, note)

    # 3) 入主表 + 明细 + 匹配
    note = DeliveryNote(
        supplier_id=supplier_id,
        source_file_id=df.id,
        note_no=parsed.note_no,
        delivery_date=parsed.delivery_date,
        total_amount=parsed.total_amount,
        ocr_model=parsed.model,
        ocr_warnings=parsed.warnings,
        ocr_confidence=parsed.confidence,
        status="pending_review",
    )
    db.add(note)
    db.flush()

    for pl in parsed.lines:
        line = DeliveryNoteLine(
            delivery_note_id=note.id, line_no=pl.line_no,
            item_name=pl.item_name, spec=pl.spec, unit=pl.unit,
            qty=pl.qty, unit_price=pl.unit_price, amount=pl.amount,
            ocr_raw_text=pl.raw_text, ocr_warnings=pl.warnings,
        )
        # 自动跑匹配
        try:
            candidates = delivery_matcher.match_line(
                db, item_name=pl.item_name, spec=pl.spec, qty=pl.qty,
                delivery_date=parsed.delivery_date,
            )
            delivery_matcher.apply_candidates_to_line(line, candidates)
        except Exception as e:  # pragma: no cover
            line.match_method = "error"
            line.match_candidates = []
            line.remark = f"匹配失败: {e}"
        db.add(line)
    db.commit()
    db.refresh(note)
    return _note_out(db, note)


@router.get("/delivery-notes/{note_id}", response_model=DeliveryNoteOut)
def get_note(note_id: int, db: Session = Depends(get_db),
             _: User = Depends(get_current_user)):
    n = db.get(DeliveryNote, note_id)
    if n is None:
        raise HTTPException(404, "送货单不存在")
    return _note_out(db, n)


@router.patch("/delivery-notes/{note_id}", response_model=DeliveryNoteOut)
def update_note(
    note_id: int, payload: DeliveryNoteUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    n = db.get(DeliveryNote, note_id)
    if n is None:
        raise HTTPException(404, "送货单不存在")
    if payload.status is not None:
        valid = {"pending_review", "confirmed", "billed", "paid", "disputed"}
        if payload.status not in valid:
            raise HTTPException(400, f"非法状态: {payload.status}")
        n.status = payload.status
        if payload.status == "paid":
            n.paid_at = datetime.now(timezone.utc)
        if payload.status == "confirmed":
            n.reconciled_at = datetime.now(timezone.utc)
    if payload.note_no is not None:
        n.note_no = payload.note_no.strip() or None
    if payload.delivery_date is not None:
        from datetime import date as _d
        try:
            n.delivery_date = _d.fromisoformat(payload.delivery_date) if payload.delivery_date else None
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
    if payload.total_amount is not None:
        n.total_amount = Decimal(str(payload.total_amount))
    if payload.remark is not None:
        n.remark = payload.remark or None
    if payload.alipay_flow_no is not None:
        n.alipay_flow_no = payload.alipay_flow_no or None
    db.commit()
    db.refresh(n)
    return _note_out(db, n)


@router.patch("/delivery-notes/{note_id}/lines/{line_id}/match", response_model=DeliveryLineOut)
def patch_line_match(
    note_id: int, line_id: int, payload: LineMatchPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """业务需求: 用户从下拉里挑一个候选 = 改成 method=manual, confidence=100 (默认)."""
    ln = db.get(DeliveryNoteLine, line_id)
    if ln is None or ln.delivery_note_id != note_id:
        raise HTTPException(404, "行不存在")
    if payload.matched_order_no is None or payload.matched_order_no == "":
        ln.matched_order_no = None
        ln.match_confidence = Decimal("0")
        ln.match_method = "none"
    else:
        ln.matched_order_no = payload.matched_order_no.strip()
        ln.match_confidence = Decimal(str(payload.match_confidence)) if payload.match_confidence is not None else Decimal("100")
        ln.match_method = "manual"
    db.commit()
    db.refresh(ln)
    return _line_out(ln)


@router.post("/delivery-notes/{note_id}/rematch", response_model=DeliveryNoteOut)
def rematch_note(
    note_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    n = db.get(DeliveryNote, note_id)
    if n is None:
        raise HTTPException(404, "送货单不存在")
    lines = db.execute(
        select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id == note_id)
    ).scalars().all()
    for line in lines:
        if line.match_method == "manual":
            continue  # 用户手动改过的不要覆盖
        candidates = delivery_matcher.match_line(
            db, item_name=line.item_name or "", spec=line.spec or "",
            qty=line.qty, delivery_date=n.delivery_date,
        )
        delivery_matcher.apply_candidates_to_line(line, candidates)
    db.commit()
    db.refresh(n)
    return _note_out(db, n)


# ----------------------------- Source image ---------------------------- #


@router.get("/delivery-notes/{note_id}/source-image")
def get_source_image(
    note_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    n = db.get(DeliveryNote, note_id)
    if n is None or n.source_file_id is None:
        raise HTTPException(404, "原图不存在")
    df = db.get(DeliveryFile, n.source_file_id)
    if df is None:
        raise HTTPException(404, "原图不存在")
    try:
        data = delivery_storage.read(df.file_path)
    except (FileNotFoundError, PermissionError) as e:
        raise HTTPException(404, str(e))
    return Response(content=data, media_type=df.mime_type or "application/octet-stream")


# ----------------------------- Folder browse (业务需求: 按月看图) ----- #


class FolderFile(BaseModel):
    id: int
    original_name: str
    mime_type: Optional[str]
    size_bytes: Optional[int]
    delivery_note_id: Optional[int]
    note_no: Optional[str]
    uploaded_at: str


class FolderListing(BaseModel):
    supplier_id: int
    year: int
    month: int
    file_count: int
    files: list[FolderFile]


@router.get("/suppliers/{supplier_id}/folders/{year}/{month}", response_model=FolderListing)
def list_folder(
    supplier_id: int, year: int, month: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    files = db.execute(
        select(DeliveryFile).where(and_(
            DeliveryFile.supplier_id == supplier_id,
            DeliveryFile.year == year,
            DeliveryFile.month == month,
        )).order_by(DeliveryFile.id.desc())
    ).scalars().all()
    # 关联回送货单 (一个文件可能对应一张单)
    notes_by_file = {
        n.source_file_id: n for n in db.execute(
            select(DeliveryNote).where(DeliveryNote.source_file_id.in_([f.id for f in files] or [-1]))
        ).scalars().all()
    }
    out: list[FolderFile] = []
    for f in files:
        n = notes_by_file.get(f.id)
        out.append(FolderFile(
            id=f.id, original_name=f.original_name or "(无名)",
            mime_type=f.mime_type, size_bytes=f.size_bytes,
            delivery_note_id=n.id if n else None,
            note_no=n.note_no if n else None,
            uploaded_at=f.created_at.isoformat(),
        ))
    return FolderListing(
        supplier_id=supplier_id, year=year, month=month,
        file_count=len(out), files=out,
    )


@router.get("/delivery-files/{file_id}/raw")
def get_file_raw(
    file_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    f = db.get(DeliveryFile, file_id)
    if f is None:
        raise HTTPException(404, "文件不存在")
    try:
        data = delivery_storage.read(f.file_path)
    except (FileNotFoundError, PermissionError) as e:
        raise HTTPException(404, str(e))
    return Response(content=data, media_type=f.mime_type or "application/octet-stream")


# ----------------------------- Statements ------------------------------ #


@router.get("/suppliers/{supplier_id}/statements/{year}/{month}.xlsx")
def download_statement_xlsx(
    supplier_id: int, year: int, month: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        data = statement_service.build_statement_data(
            db, supplier_id=supplier_id, year=year, month=month,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    xlsx = statement_service.render_excel(data)
    filename = f"{data.supplier.name}-{year}-{month:02d}-对账单.xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
        },
    )


@router.get("/suppliers/{supplier_id}/statements/{year}/{month}.html",
            response_class=HTMLResponse)
def view_statement_html(
    supplier_id: int, year: int, month: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        data = statement_service.build_statement_data(
            db, supplier_id=supplier_id, year=year, month=month,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return HTMLResponse(content=statement_service.render_html(data))


def _url_quote(name: str) -> str:
    from urllib.parse import quote
    return quote(name)

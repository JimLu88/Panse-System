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
from sqlalchemy import and_, func, or_, select
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
from app.services import (
    delivery_matcher, delivery_note_service, delivery_storage, ocr_service, statement_service,
)

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
    alipay_counterparty_keywords: list[str] = []
    alipay_account: Optional[str] = None
    # 最新一期供应商评分 (整合进供应商页, 不必再单开评分页)
    latest_score: Optional[float] = None
    latest_rank: Optional[int] = None
    score_period: Optional[str] = None


class SupplierIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    supplier_type: str = "other"
    contact: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    remark: Optional[str] = None
    alipay_counterparty_keywords: list[str] = []
    alipay_account: Optional[str] = None


class SupplierPatch(BaseModel):
    name: Optional[str] = None
    supplier_type: Optional[str] = None
    contact: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    is_active: Optional[bool] = None
    remark: Optional[str] = None
    alipay_counterparty_keywords: Optional[list[str]] = None
    alipay_account: Optional[str] = None


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


def _supplier_out(s: Supplier, sc=None) -> SupplierOut:
    return SupplierOut(
        id=s.id, name=s.name, supplier_type=s.supplier_type,
        contact=s.contact, phone=s.phone, address=s.address,
        payment_terms=s.payment_terms, is_active=s.is_active, remark=s.remark,
        alipay_counterparty_keywords=list(s.alipay_counterparty_keywords or []),
        alipay_account=s.alipay_account,
        latest_score=float(sc.score) if sc is not None and sc.score is not None else None,
        latest_rank=sc.rank if sc is not None else None,
        score_period=f"{sc.year}-{sc.month:02d}" if sc is not None else None,
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
    # 每个供应商取最新一期评分 (year,month 最大)
    from app.models.supplier_score import SupplierScore
    latest: dict[int, SupplierScore] = {}
    for sc in db.execute(
        select(SupplierScore).order_by(SupplierScore.year.desc(), SupplierScore.month.desc())
    ).scalars().all():
        latest.setdefault(sc.supplier_id, sc)
    return [_supplier_out(s, latest.get(s.id)) for s in rows]


class SupplierScoreHistoryOut(BaseModel):
    year: int
    month: int
    period: str
    score: Optional[float] = None
    rank: Optional[int] = None
    on_time_rate: Optional[float] = None
    return_rate: Optional[float] = None
    price_variance_pct: Optional[float] = None
    total_orders: int = 0
    total_amount: Optional[float] = None
    detail: dict = {}


@router.get("/suppliers/{supplier_id}/scores", response_model=list[SupplierScoreHistoryOut])
def supplier_score_history(
    supplier_id: int,
    limit: int = Query(12, ge=1, le=36),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """某供应商最近 N 期评分(新→旧) + 各维度明细 — 供供应商详情页评分卡 + 趋势。"""
    from app.services import supplier_score_service
    rows = supplier_score_service.history_for_supplier(db, supplier_id, limit=limit)
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    return [SupplierScoreHistoryOut(
        year=r.year, month=r.month, period=f"{r.year}-{r.month:02d}",
        score=f(r.score), rank=r.rank,
        on_time_rate=f(r.on_time_rate), return_rate=f(r.return_rate),
        price_variance_pct=f(r.price_variance_pct),
        total_orders=r.total_orders, total_amount=f(r.total_amount),
        detail=r.detail_json or {},
    ) for r in rows]


@router.post("/suppliers/recompute-scores")
def recompute_supplier_scores(
    year: int = Query(...), month: int = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """手动重算某月全部供应商评分(默认调度器每月1号自动跑, 这里给「立即重算」按钮用)。"""
    from app.services import supplier_score_service
    rows = supplier_score_service.compute_for_month(db, year, month)
    db.commit()
    return {"computed": len(rows), "year": year, "month": month}


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


class AutoCreateSuppliersIn(BaseModel):
    counterparties: list[str]
    supplier_type: str = "other"


# 非供应商的常见对手方关键词 (过滤噪音, 不当候选)
# 内部人员/账户 (魏佳英/魏佳音/爱群/畔色 + 掩码 **英/**群/**音) 统一取自 internal_accounts
from app.services.internal_accounts import INTERNAL_COUNTERPARTY_KW

_NON_SUPPLIER_KW = (
    "淘宝", "天猫", "淘天", "支付宝", "余额宝", "红包", "退款", "还款", "手续费", "服务费",
    "工资", "个人", "转账", "提现", "花呗", "借呗", "微信", "财付通", "保证金", "理财", "申购",
) + INTERNAL_COUNTERPARTY_KW


@router.get("/suppliers/alipay-candidates")
def alipay_supplier_candidates(
    min_count: int = Query(2, ge=1, description="至少出账几次才算候选"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """从支付宝流水挖出"还不是供应商、但多次给它打过款"的对手方, 供自动建供应商。

    出账(amount<0)按 counterparty 聚合; 跳过已是供应商 / 关键字命中 / 明显非供应商的。
    """
    from app.models.finance import AlipayFlow
    known: set[str] = set()
    for s in db.execute(select(Supplier)).scalars().all():
        known.add(s.name)
        for kw in (s.alipay_counterparty_keywords or []):
            if kw:
                known.add(kw)
    rows = db.execute(
        select(
            AlipayFlow.counterparty,
            func.count().label("cnt"),
            func.coalesce(func.sum(func.abs(AlipayFlow.amount)), 0).label("total"),
        )
        .where(
            AlipayFlow.amount < 0,
            AlipayFlow.counterparty.isnot(None), AlipayFlow.counterparty != "",
            # 排除内部理财/余额宝转入(如魏佳音的 18 笔理财申购, 钱没出去)
            or_(AlipayFlow.transaction_type.is_(None), ~AlipayFlow.transaction_type.like("%理财%")),
        )
        .group_by(AlipayFlow.counterparty)
    ).all()
    cands = []
    for cp, cnt, total in rows:
        if cnt < min_count:
            continue
        if cp in known or any(k and (k in cp or cp in k) for k in known):
            continue
        if any(nk in cp for nk in _NON_SUPPLIER_KW):
            continue
        cands.append({"counterparty": cp, "payment_count": int(cnt), "total_paid": float(total or 0)})
    cands.sort(key=lambda c: c["total_paid"], reverse=True)
    return {"candidates": cands, "total": len(cands)}


@router.post("/suppliers/auto-create")
def auto_create_suppliers(
    payload: AutoCreateSuppliersIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """把选中的对手方批量建成供应商 (名字=对手方, 关键字=[对手方], 类型可选)。"""
    existing = {n for (n,) in db.execute(select(Supplier.name)).all()}
    created = []
    for cp in payload.counterparties:
        cp = (cp or "").strip()
        if not cp or cp in existing:
            continue
        db.add(Supplier(
            name=cp, supplier_type=payload.supplier_type or "other",
            alipay_counterparty_keywords=[cp], is_active=True,
        ))
        existing.add(cp)
        created.append(cp)
    if created:
        db.commit()
    return {"created": created, "count": len(created)}


@router.get("/suppliers/purchase-candidates")
def purchase_supplier_candidates(
    min_count: int = Query(1, ge=1, description="至少出现几次才算候选"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """从配件采购记录(PartPurchase.supplier)挖出还不是供应商的真实供应商 —— 比支付宝准。

    采购记录里的供应商才是真实付款对象(老孙木皮厂/木隅工厂…);
    跳过已是供应商 / 非供应商关键字(代扣/理财/淘天等)的。返回结构与支付宝候选一致, 前端可复用建档。
    """
    from app.models.order import PartPurchase
    known: set[str] = set()
    for s in db.execute(select(Supplier)).scalars().all():
        known.add(s.name)
        for kw in (s.alipay_counterparty_keywords or []):
            if kw:
                known.add(kw)
    rows = db.execute(
        select(
            PartPurchase.supplier,
            func.count().label("cnt"),
            func.coalesce(func.sum(func.abs(PartPurchase.amount)), 0).label("total"),
        )
        .where(
            PartPurchase.supplier.isnot(None), PartPurchase.supplier != "",
            # 排除非采购行(代扣/理财/服务费等), 与配件采购列表口径一致
            or_(PartPurchase.material_name.is_(None),
                and_(*[PartPurchase.material_name.notlike(f"%{k}%")
                       for k in ("代扣", "理财", "申购", "服务费", "手续费", "余额宝")])),
        )
        .group_by(PartPurchase.supplier)
    ).all()
    cands = []
    for sup, cnt, total in rows:
        if cnt < min_count:
            continue
        if sup in known or any(k and (k in sup or sup in k) for k in known):
            continue
        if any(nk in sup for nk in _NON_SUPPLIER_KW):
            continue
        cands.append({"counterparty": sup, "payment_count": int(cnt), "total_paid": float(total or 0)})
    cands.sort(key=lambda c: c["total_paid"], reverse=True)
    return {"candidates": cands, "total": len(cands)}


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

    from datetime import date as _d
    archive_date = None
    if on_date:
        try:
            archive_date = _d.fromisoformat(on_date)
        except ValueError:
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD")

    note = delivery_note_service.create_from_image(
        db, supplier=s, content=content, mime=file.content_type,
        original_name=file.filename or "upload.jpg",
        uploaded_by=user.username, on_date=archive_date,
    )
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


# ----------------------------- 支付宝自动对账 (业务需求 2) ----------- #


class PaymentMatchOut(BaseModel):
    flow_id: int
    flow_no: str
    flow_amount: float
    flow_time: Optional[str]
    counterparty: Optional[str]
    supplier_id: Optional[int]
    supplier_name: Optional[str]
    matched_note_ids: list[int]
    matched_note_nos: list[str]
    decision: str  # exact / combo / needs_review / no_supplier / no_candidates / skipped
    reason: str


class ReconcileSummary(BaseModel):
    scanned: int
    matched_count: int
    needs_review: int
    no_supplier: int
    no_candidates: int
    skipped: int
    matches: list[PaymentMatchOut]


class ReconcileRequest(BaseModel):
    account: Optional[str] = None
    since_days: int = 90
    dry_run: bool = False


@router.post("/suppliers/reconcile-payments", response_model=ReconcileSummary)
def reconcile_payments(
    payload: ReconcileRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """业务需求: 扫支付宝 factory_payment 流水, 自动配到供应商送货单。

    dry_run=True 只预览匹配方案不落盘。
    匹配成功 → 流水标 matched, 单据 → paid + 写 alipay_flow_no。
    """
    from app.services import supplier_payment_matcher
    result = supplier_payment_matcher.reconcile(
        db, account=payload.account,
        since_days=payload.since_days, dry_run=payload.dry_run,
    )
    if not payload.dry_run:
        db.commit()
    return ReconcileSummary(
        scanned=result.scanned,
        matched_count=result.matched_count,
        needs_review=result.needs_review,
        no_supplier=result.no_supplier,
        no_candidates=result.no_candidates,
        skipped=result.skipped,
        matches=[
            PaymentMatchOut(
                flow_id=m.flow_id, flow_no=m.flow_no,
                flow_amount=float(m.flow_amount),
                flow_time=m.flow_time.isoformat() if m.flow_time else None,
                counterparty=m.counterparty,
                supplier_id=m.supplier_id, supplier_name=m.supplier_name,
                matched_note_ids=m.matched_note_ids,
                matched_note_nos=m.matched_note_nos,
                decision=m.decision, reason=m.reason,
            )
            for m in result.matches
        ],
    )


class ManualPaymentMatchIn(BaseModel):
    flow_id: int
    note_ids: list[int]


@router.post("/suppliers/reconcile-payments/manual", response_model=PaymentMatchOut)
def apply_manual_payment_match(
    payload: ManualPaymentMatchIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """needs_review 的流水, 用户在 UI 选好后调这个 endpoint 确认落盘."""
    from app.services import supplier_payment_matcher
    try:
        m = supplier_payment_matcher.apply_manual_match(
            db, flow_id=payload.flow_id, note_ids=payload.note_ids,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return PaymentMatchOut(
        flow_id=m.flow_id, flow_no=m.flow_no,
        flow_amount=float(m.flow_amount),
        flow_time=m.flow_time.isoformat() if m.flow_time else None,
        counterparty=m.counterparty,
        supplier_id=m.supplier_id, supplier_name=m.supplier_name,
        matched_note_ids=m.matched_note_ids,
        matched_note_nos=m.matched_note_nos,
        decision=m.decision, reason=m.reason,
    )

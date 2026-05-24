from datetime import date as _date
from decimal import Decimal as _Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order
from app.schemas.order import (
    CsvImportReport,
    OrderCreate,
    OrderOut,
    OrderStatusChange,
    OrderUpdate,
)
from app.services import data_quality_service, exception_service, factory_sheet, order_import, order_service

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("", response_model=list[OrderOut])
def list_orders(
    q: Optional[str] = Query(None, description="搜索订单号/客户名"),
    status: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(Order)
    if q:
        stmt = stmt.where(or_(Order.order_no.ilike(f"%{q}%"), Order.customer_name.ilike(f"%{q}%")))
    if status:
        stmt = stmt.where(Order.status == status)
    if platform:
        stmt = stmt.where(Order.platform == platform)
    stmt = stmt.order_by(Order.order_date.desc().nulls_last(), Order.id.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=OrderOut, status_code=201)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    existing = db.execute(select(Order).where(Order.order_no == payload.order_no)).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"order {payload.order_no} already exists")
    o = Order(**payload.model_dump(), status="pending_payment")
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


@router.patch("/{order_id}", response_model=OrderOut)
def update_order(order_id: int, payload: OrderUpdate, db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(o, k, v)
    db.commit()
    db.refresh(o)
    return o


@router.post("/{order_id}/status", response_model=OrderOut)
def change_status(order_id: int, payload: OrderStatusChange, db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    try:
        order_service.transition(db, o, payload.status, actor=payload.actor, force=payload.force)
    except order_service.InvalidStatusTransition as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(o)
    return o


@router.post("/import-csv", response_model=CsvImportReport)
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")  # 中文 Excel 导出常见
    report = order_import.import_orders_from_csv(db, text)
    return CsvImportReport(
        inserted=report.inserted,
        skipped_duplicate=report.skipped_duplicate,
        skipped_invalid=report.skipped_invalid,
        errors=report.errors,
    )


class FactorySheetMaterialOut(BaseModel):
    material_code: str
    material_name: Optional[str]
    qty_per_product: _Decimal
    total_qty: _Decimal
    unit: Optional[str]
    spec: Optional[str]


class FactorySheetWarningOut(BaseModel):
    code: str
    message: str
    severity: str


class FactorySheetOut(BaseModel):
    order_no: str
    sheet_title: str
    order_date: Optional[_date]
    ship_date: Optional[_date]
    product_code: Optional[str]
    product_name: Optional[str]
    sku: Optional[str]
    sku_code: Optional[str]
    image_url: Optional[str]
    material_desc: Optional[str]
    dimension_desc: Optional[str]
    customer_name: Optional[str]
    customer_phone: Optional[str]
    customer_address: Optional[str]
    qty: int
    remark: Optional[str]
    materials: list[FactorySheetMaterialOut]
    is_custom_variant: bool
    dimension_changes: Optional[dict]
    warnings: list[FactorySheetWarningOut]


@router.post("/{order_id}/generate-factory-order")
def generate_factory_order(order_id: int, db: Session = Depends(get_db)):
    """业务需求 2/3: 从平台 Order 自动派生 FactoryOrder + 锁 BOM 库存.

    幂等. 已生成时返回已有的 FactoryOrder. 缺货时仍会创建工厂单, 但同时生成 critical Alert。
    """
    from app.services import factory_order_service
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    if o.is_historical:
        raise HTTPException(400, "历史订单不参与工厂派生")
    try:
        fo, lock = factory_order_service.generate_factory_order_for(db, o)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {
        "factory_order_id": fo.id,
        "factory_order_no": fo.factory_order_no,
        "locked_lines": lock.locked_lines,
        "shortages": lock.shortages,
        "alerts_created": lock.alerts_created,
    }


class CreateFutureOrderIn(BaseModel):
    base_order_no: str
    activate_at: str   # ISO datetime
    product_code: Optional[str] = None
    sku: Optional[str] = None
    qty: int = 1
    customer_name: Optional[str] = None
    remark: Optional[str] = None
    platform: str = "淘宝"


@router.post("/future")
def create_future_order(payload: CreateFutureOrderIn, db: Session = Depends(get_db)):
    """业务需求 10 选项 A: 派生一个 30 天后激活的远期订单."""
    from datetime import datetime as _dt
    from app.services import factory_order_service
    try:
        activate = _dt.fromisoformat(payload.activate_at)
    except ValueError:
        raise HTTPException(400, "activate_at 不是合法 ISO 时间")
    o = factory_order_service.create_future_order(
        db,
        base_order_no=payload.base_order_no,
        activate_at=activate,
        platform=payload.platform,
        product_code=payload.product_code,
        sku=payload.sku,
        qty=payload.qty,
        customer_name=payload.customer_name,
        remark=payload.remark,
    )
    db.commit()
    return {"id": o.id, "order_no": o.order_no, "activate_at": o.activate_at.isoformat()}


class VoidFactoryOrderIn(BaseModel):
    reason: str


class OrderEventOut(BaseModel):
    id: int
    order_id: int
    kind: str
    actor: Optional[str]
    summary: str
    detail: Optional[str]
    context_json: Optional[dict]
    created_at: str


@router.get("/{order_id}/timeline", response_model=list[OrderEventOut])
def get_order_timeline(order_id: int, db: Session = Depends(get_db)):
    """Phase 8 Tier 1 #2: 订单全生命周期时间轴 (状态变化 + 工厂单 + 库存锁定 + 退货 + 评论)."""
    from app.services import order_event_service
    events = order_event_service.list_for_order(db, order_id)
    return [
        OrderEventOut(
            id=e.id, order_id=e.order_id, kind=e.kind, actor=e.actor,
            summary=e.summary, detail=e.detail,
            context_json=e.context_json,
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]


class CommentIn(BaseModel):
    text: str


@router.post("/{order_id}/comments", response_model=OrderEventOut)
def add_comment(order_id: int, payload: CommentIn,
                db: Session = Depends(get_db)):
    """Phase 8: 在订单时间轴留评论."""
    if not payload.text.strip():
        raise HTTPException(400, "评论不能为空")
    from app.services import order_event_service
    e = order_event_service.record(
        db, order_id=order_id, kind="comment",
        actor="user", summary=payload.text.strip()[:200],
        detail=payload.text.strip(),
    )
    db.commit()
    return OrderEventOut(
        id=e.id, order_id=e.order_id, kind=e.kind, actor=e.actor,
        summary=e.summary, detail=e.detail, context_json=e.context_json,
        created_at=e.created_at.isoformat(),
    )


@router.post("/factory-orders/{factory_order_id}/void")
def void_factory_order(
    factory_order_id: int, payload: VoidFactoryOrderIn, db: Session = Depends(get_db),
):
    """业务需求 11: 作废一个工厂下单单 (会同时释放锁定库存)."""
    from app.services import factory_order_service
    fo = factory_order_service.void_factory_order(
        db, factory_order_id, reason=payload.reason,
    )
    if fo is None:
        raise HTTPException(404, "factory order not found")
    db.commit()
    return {
        "id": fo.id,
        "factory_order_no": fo.factory_order_no,
        "voided_at": fo.voided_at.isoformat() if fo.voided_at else None,
        "voided_reason": fo.voided_reason,
    }


@router.get("/{order_id}/factory-sheet", response_model=FactorySheetOut)
def get_factory_sheet(order_id: int, db: Session = Depends(get_db)):
    """业务需求 §1: 生成制单图数据 (前端渲染打印).

    自动拉 BOM 物料明细 + 加密地址检测 + 定制变更信息。
    """
    try:
        sheet = factory_sheet.build(db, order_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FactorySheetOut(
        order_no=sheet.order_no,
        sheet_title=sheet.sheet_title,
        order_date=sheet.order_date,
        ship_date=sheet.ship_date,
        product_code=sheet.product_code,
        product_name=sheet.product_name,
        sku=sheet.sku,
        sku_code=sheet.sku_code,
        image_url=sheet.image_url,
        material_desc=sheet.material_desc,
        dimension_desc=sheet.dimension_desc,
        customer_name=sheet.customer_name,
        customer_phone=sheet.customer_phone,
        customer_address=sheet.customer_address,
        qty=sheet.qty,
        remark=sheet.remark,
        materials=[FactorySheetMaterialOut(**m.__dict__) for m in sheet.materials],
        is_custom_variant=sheet.is_custom_variant,
        dimension_changes=sheet.dimension_changes,
        warnings=[FactorySheetWarningOut(**w.__dict__) for w in sheet.warnings],
    )


@router.post("/{order_id}/confirm-tracking", response_model=OrderOut)
def confirm_tracking(order_id: int, db: Session = Depends(get_db)):
    """双核对签收: 物流确认 (有物流单号 + 人工确认快递已派送)."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    o.tracking_confirmed = True
    _check_signoff(db, o)
    db.commit()
    db.refresh(o)
    return o


@router.post("/{order_id}/confirm-manual", response_model=OrderOut)
def confirm_manual(order_id: int, db: Session = Depends(get_db)):
    """双核对签收: 人工确认签收 (客户反馈/内部确认)."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    o.manual_confirmed = True
    _check_signoff(db, o)
    db.commit()
    db.refresh(o)
    return o


def _check_signoff(db, o: Order) -> None:
    """两个核对都完成 → 状态迁移到 signed; 缺一 → 标记有疑问并写异常."""
    if o.tracking_confirmed and o.manual_confirmed:
        o.signoff_questioned = False
        if o.status == "shipped":
            try:
                order_service.transition(db, o, "signed", actor="auto_signoff")
            except order_service.InvalidStatusTransition:
                pass
    else:
        o.signoff_questioned = True
        exception_service.record(
            db,
            source_table="orders",
            source_pk=o.id,
            exception_type="signoff_questioned",
            severity="warning",
            description=f"订单 {o.order_no} 签收核对未完整: 物流确认={o.tracking_confirmed}, 人工确认={o.manual_confirmed}。",
            suggestion_action="请完成物流确认和人工确认两个核对环节。",
            context={"order_no": o.order_no, "tracking_confirmed": o.tracking_confirmed, "manual_confirmed": o.manual_confirmed},
        )

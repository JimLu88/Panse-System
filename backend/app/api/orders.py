from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
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
from app.services import order_import, order_service

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

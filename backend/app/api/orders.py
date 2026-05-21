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
from app.services import factory_sheet, order_import, order_service

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

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.inventory import ProductInventory
from app.models.product import Product
from app.schemas.product_inventory import (
    ProductInventoryCreate,
    ProductInventoryOut,
    ProductInventoryWithStats,
)
from app.services import exception_service, product_inventory_service


class ProductInventoryPatch(BaseModel):
    qty: Optional[Decimal] = None
    locked_qty: Optional[Decimal] = None
    safety_stock: Optional[Decimal] = None
    lead_time_days: Optional[int] = None
    slow_moving_days: Optional[int] = None
    reorder_point: Optional[Decimal] = None
    remark: Optional[str] = None

router = APIRouter(prefix="/api/inventory/products", tags=["inventory"])


@router.get("", response_model=list[ProductInventoryWithStats])
def list_product_inventory(
    warehouse: Optional[str] = None,
    product_code: Optional[str] = None,
    warning_only: bool = Query(False, description="只显示需要关注的库存 (warning/danger/critical/excess)"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(ProductInventory)
    if warehouse:
        stmt = stmt.where(ProductInventory.warehouse == warehouse)
    if product_code:
        stmt = stmt.where(ProductInventory.product_code == product_code)
    stmt = stmt.order_by(ProductInventory.product_code, ProductInventory.sku).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()

    result = []
    for inv in rows:
        stats = product_inventory_service.compute_product_stats(db, inv)
        if warning_only and stats["warning_status"] == "ok":
            continue
        row_dict = {
            "id": inv.id,
            "warehouse": inv.warehouse,
            "product_code": inv.product_code,
            "sku": inv.sku,
            "spec": inv.spec,
            "unit": inv.unit,
            "physical_qty": inv.physical_qty,
            "locked_qty": inv.locked_qty,
            "safety_stock": inv.safety_stock,
            "lead_time_days": inv.lead_time_days,
            "slow_moving_days": inv.slow_moving_days,
            "reorder_point": inv.reorder_point,
            "remark": inv.remark,
            **stats,
        }
        result.append(ProductInventoryWithStats(**row_dict))
    return result


@router.post("/refresh", response_model=dict)
def refresh_inventory_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """把从订单历史推算的提前期/安全库存/预警线批量回写到库存表（幂等）。"""
    n = product_inventory_service.refresh_all_inventory(db)
    db.commit()
    return {"updated": n, "message": f"已更新 {n} 条成品库存推算字段"}


@router.post("", response_model=ProductInventoryOut, status_code=201)
def add_product_inventory_row(payload: ProductInventoryCreate, db: Session = Depends(get_db)):
    product = db.execute(
        select(Product).where(Product.code == payload.product_code)
    ).scalar_one_or_none()
    if product is None:
        exception_service.record(
            db,
            source_table="product_inventory",
            source_pk=payload.product_code,
            exception_type="unknown_product_code",
            severity="error",
            description=(
                f"录入成品库存时引用了不存在的产品编码 {payload.product_code}。"
                f"请先到「产品总表」补登该产品，或检查编码是否拼错。"
            ),
            suggestion_action="create_or_correct_product",
            context={"warehouse": payload.warehouse, "sku": payload.sku},
        )

    inv = ProductInventory(
        warehouse=payload.warehouse,
        product_code=payload.product_code,
        sku=payload.sku,
        spec=payload.spec,
        unit=payload.unit,
        physical_qty=payload.physical_qty,
        locked_qty=payload.locked_qty,
        safety_stock=payload.safety_stock,
        lead_time_days=payload.lead_time_days,
        slow_moving_days=payload.slow_moving_days,
        reorder_point=payload.reorder_point,
        remark=payload.remark,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


@router.patch("/{inventory_id}", response_model=ProductInventoryOut)
def update_product_inventory(
    inventory_id: int,
    payload: ProductInventoryPatch,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """盘库调整：修改成品库存数量及参数。"""
    inv = db.get(ProductInventory, inventory_id)
    if not inv:
        raise HTTPException(404, "inventory row not found")
    if payload.qty is not None:
        inv.physical_qty = payload.qty
    if payload.locked_qty is not None:
        inv.locked_qty = payload.locked_qty
    if payload.safety_stock is not None:
        inv.safety_stock = payload.safety_stock
    if payload.lead_time_days is not None:
        inv.lead_time_days = payload.lead_time_days
    if payload.slow_moving_days is not None:
        inv.slow_moving_days = payload.slow_moving_days
    if payload.reorder_point is not None:
        inv.reorder_point = payload.reorder_point
    if payload.remark is not None:
        inv.remark = payload.remark
    db.commit()
    db.refresh(inv)
    return inv


@router.delete("/{inventory_id}", status_code=204)
def delete_product_inventory(inventory_id: int, db: Session = Depends(get_db)):
    inv = db.get(ProductInventory, inventory_id)
    if not inv:
        raise HTTPException(404, "inventory row not found")
    db.delete(inv)
    db.commit()

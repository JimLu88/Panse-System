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
from app.schemas.product_inventory import ProductInventoryCreate, ProductInventoryOut
from app.services import exception_service


class ProductInventoryPatch(BaseModel):
    qty: Optional[Decimal] = None
    remark: Optional[str] = None

router = APIRouter(prefix="/api/inventory/products", tags=["inventory"])


@router.get("", response_model=list[ProductInventoryOut])
def list_product_inventory(
    warehouse: Optional[str] = None,
    product_code: Optional[str] = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(ProductInventory)
    if warehouse:
        stmt = stmt.where(ProductInventory.warehouse == warehouse)
    if product_code:
        stmt = stmt.where(ProductInventory.product_code == product_code)
    stmt = stmt.order_by(ProductInventory.id.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=ProductInventoryOut, status_code=201)
def add_product_inventory_row(payload: ProductInventoryCreate, db: Session = Depends(get_db)):
    # 产品级走精确编码匹配 — 不像配件那样自动建定制。
    # 如果产品不存在，直接进异常表提示，但不阻断录入（库存先建好，产品由人补登）。
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
    """盘库调整: 修改成品库存数量."""
    inv = db.get(ProductInventory, inventory_id)
    if not inv:
        raise HTTPException(404, "inventory row not found")
    if payload.qty is not None:
        inv.physical_qty = payload.qty
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

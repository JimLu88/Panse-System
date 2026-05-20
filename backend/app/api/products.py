from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate
from app.services import product_coder

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=list[ProductOut])
def list_products(
    q: Optional[str] = Query(None),
    brand: Optional[str] = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(Product)
    if q:
        stmt = stmt.where(or_(Product.code.ilike(f"%{q}%"), Product.name.ilike(f"%{q}%")))
    if brand:
        stmt = stmt.where(Product.brand == brand)
    stmt = stmt.order_by(Product.code).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=ProductOut, status_code=201)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    try:
        code = product_coder.next_product_code(
            db,
            brand=payload.brand,
            category=payload.category,
            created_at=payload.created_on,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    prod = Product(
        code=code,
        name=payload.name,
        brand=payload.brand.upper(),
        category=payload.category_label or payload.category,
        remark=payload.remark,
    )
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return prod


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db)):
    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404, "product not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(prod, k, v)
    db.commit()
    db.refresh(prod)
    return prod

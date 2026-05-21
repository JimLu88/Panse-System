from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate, TaobaoIdsUpdate
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
        taobao_id=payload.taobao_id,
        alt_taobao_ids=payload.alt_taobao_ids or [],
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


@router.put("/{product_id}/taobao-ids", response_model=ProductOut)
def update_taobao_ids(
    product_id: int, payload: TaobaoIdsUpdate, db: Session = Depends(get_db)
):
    """业务需求 §4: 一个产品最多配 5 个备选商品 ID, 链接换了不用改其它表."""
    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404, "product not found")
    if len(payload.alternatives) > 5:
        raise HTTPException(400, "alternatives 最多 5 个")
    prod.taobao_id = payload.primary
    prod.alt_taobao_ids = payload.alternatives
    db.commit()
    db.refresh(prod)
    return prod


@router.get("/lookup-by-taobao-id/{taobao_id}", response_model=ProductOut)
def lookup_by_taobao_id(taobao_id: str, db: Session = Depends(get_db)):
    """业务需求 §3+§4: OCR 拿到淘宝商品 ID 后, 找对应产品.

    先按 primary id 命中; 再按 alt_taobao_ids 数组命中。
    """
    primary = db.execute(
        select(Product).where(Product.taobao_id == taobao_id)
    ).scalar_one_or_none()
    if primary:
        return primary
    # 备选 ID — 由于 JSON 数组在 SQLite 上没有 ANY 操作, 全扫匹配
    all_prods = db.execute(
        select(Product).where(Product.alt_taobao_ids.isnot(None))
    ).scalars().all()
    for p in all_prods:
        if p.alt_taobao_ids and taobao_id in p.alt_taobao_ids:
            return p
    raise HTTPException(404, f"没有产品绑定到淘宝商品 ID {taobao_id}")

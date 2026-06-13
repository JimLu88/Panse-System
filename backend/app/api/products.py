from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.product import Product
from app.api.pricing import PricingSkuOut
from app.models.pricing import PricingSku
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate, TaobaoIdsUpdate
from app.services import product_coder, product_match_service

router = APIRouter(prefix="/api/products", tags=["products"])


class ProductMatchOut(BaseModel):
    product_code: Optional[str]
    product_name: Optional[str]
    sku_code: Optional[str]
    sku: Optional[str]
    confidence: float


class RankedSkuOut(BaseModel):
    sku_code: Optional[str]
    sku: Optional[str]
    size_category: Optional[str]
    confidence: float


class RankedProductOut(BaseModel):
    product_code: str
    product_name: str
    product_confidence: float
    skus: list[RankedSkuOut]


@router.get("", response_model=list[ProductOut])
def list_products(
    q: Optional[str] = Query(None),
    brand: Optional[str] = None,
    category: Optional[str] = Query(None, description="按类目精确筛"),
    sort: Optional[str] = Query(None, description="recent=按最近更新倒序 (新产品录入参考下拉用)"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(Product)
    if q:
        # 全站统一模糊搜索: 「榉木餐桌」也能搜到「榉木岩板餐桌」(规则见 fuzzy_search)
        from app.services.fuzzy_search import fuzzy_clause
        fc = fuzzy_clause(q, like_cols=[Product.code, Product.name, Product.sub_name],
                          gap_cols=[Product.name, Product.sub_name])
        if fc is not None:
            stmt = stmt.where(fc)
    if brand:
        stmt = stmt.where(Product.brand == brand)
    if category:
        stmt = stmt.where(Product.category == category)
    if sort == "recent":
        stmt = stmt.order_by(Product.updated_at.desc())
    else:
        stmt = stmt.order_by(Product.code)
    stmt = stmt.limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    # 产品行图片图库优先 (用户拍板 2026-06-12: 图片显示全部图库优先)。
    # 只注入显示字段, 不改 image_url 数据; 图库根目录整批只扫一次。
    from app.services.gallery_lookup import main_image_url_map
    gallery_urls = main_image_url_map([r.code for r in rows])
    out = []
    for r in rows:
        base = ProductOut.model_validate(r)
        base.gallery_image_url = gallery_urls.get(r.code)
        out.append(base)
    return out


@router.get("/categories", response_model=list[str])
def list_categories(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """所有出现过的产品类目 (去重排序) — 产品/BOM/定价 三处按类目筛的下拉数据源。"""
    rows = db.execute(
        select(Product.category)
        .where(Product.category.isnot(None), Product.category != "")
        .distinct()
        .order_by(Product.category)
    ).scalars().all()
    return [c for c in rows if c]


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


_PRODUCT_FIELD_LABELS = {
    "name": "产品名称", "sub_name": "副名称", "brand": "品牌", "category": "类目",
    "priority": "重要程度", "remark": "备注", "main_material": "主材", "aux_material": "辅材",
    "size_detail": "尺寸明细", "size_value": "尺寸值", "custom_scope": "定制范围",
    "accessory_desc": "外配件说明", "accessory_remark": "配件备注", "description": "产品文案",
    "listing_status": "上架状态",
}


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db)):
    """编辑产品 (图4): 改动逐字段记修改档案 (table=products, 字段级保留最近30份, 可悬浮回看)。
    产品主数据为单一来源, 保存后该产品所有 SKU / 订单的下单图/核算自动用新值。"""
    from app.services import field_change_service
    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404, "product not found")
    field_change_service.diff_and_apply(
        db, prod, payload.model_dump(exclude_unset=True),
        table="products", pk=prod.code, actor="产品编辑", source="web",
        row_label=(prod.name or prod.code)[:40], field_labels=_PRODUCT_FIELD_LABELS,
    )
    db.commit()
    db.refresh(prod)
    return prod


@router.delete("/{product_id}")
def delete_product(product_id: int, force: bool = Query(False), db: Session = Depends(get_db)):
    """删产品 + 级联删它的 BOM 行 / 定价 SKU。

    防误删: 被订单引用时拦截(409), 确认后加 ?force=true 才删。用于清理重复/错误产品
    (例如一个 SKU 编码错挂了两个产品里的多余那个)。
    """
    from app.models.bom import BomLine
    from app.models.order import Order
    from app.models.pricing import PricingSku

    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404, "product not found")
    n_orders = db.execute(
        select(func.count()).select_from(Order).where(Order.product_code == prod.code)
    ).scalar() or 0
    if n_orders and not force:
        raise HTTPException(
            409,
            f"该产品被 {n_orders} 个订单引用, 删除会影响这些订单的成本/配件核算。"
            f"确认要删请加 force=true。",
        )
    n_bom = db.query(BomLine).filter(BomLine.product_code == prod.code).delete(synchronize_session=False)
    n_sku = db.query(PricingSku).filter(PricingSku.product_code == prod.code).delete(synchronize_session=False)
    code = prod.code
    db.delete(prod)
    db.commit()
    return {"deleted_product": code, "deleted_bom_lines": n_bom,
            "deleted_pricing_skus": n_sku, "orders_referencing": n_orders}


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


@router.get("/match", response_model=ProductMatchOut)
def match_product(
    product_name: str = Query(""),
    sku_text: str = Query("", alias="sku"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """截图录单 / 微定制 AI 用: 模糊匹配系统产品 + SKU (返回最佳单条)."""
    return product_match_service.match(db, product_name, sku_text)


@router.get("/match-ranked", response_model=list[RankedProductOut])
def match_product_ranked(
    product_name: str = Query(""),
    sku_text: str = Query("", alias="sku"),
    limit: int = Query(10, le=50),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """微定制: 按匹配度返回 Top-N 产品(一级) + 各自 SKU(二级), 供人工挑选."""
    return product_match_service.match_ranked(db, product_name, sku_text, limit=limit)


@router.get("/{product_code}/skus", response_model=list[PricingSkuOut])
def list_product_skus(
    product_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """产品主数据中心: 展开 SKU 列表 (SKU 图片图库优先, 用户拍板 2026-06-12)."""
    rows = db.execute(
        select(PricingSku)
        .where(PricingSku.product_code == product_code)
        .order_by(PricingSku.sku_code)
    ).scalars().all()
    from app.services.gallery_lookup import sku_gallery_url_map
    gallery_urls = sku_gallery_url_map(
        [(r.product_code, r.sku_code, r.sku) for r in rows])
    out = []
    for r in rows:
        base = PricingSkuOut.model_validate(r).model_dump()
        base["gallery_image_url"] = gallery_urls.get(r.sku_code)
        out.append(PricingSkuOut.model_validate(base))
    return out


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

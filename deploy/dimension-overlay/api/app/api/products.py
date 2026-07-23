from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.product import Product
from app.models.product_dimension import ProductDimensionAsset
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
    dimension_counts: dict[str, dict[str, int]] = {}
    if rows:
        codes = [r.code for r in rows]
        for code, status in db.execute(
            select(ProductDimensionAsset.product_code, ProductDimensionAsset.mapping_status)
            .where(ProductDimensionAsset.product_code.in_(codes))
        ).all():
            counts = dimension_counts.setdefault(code, {"total": 0, "review": 0})
            counts["total"] += 1
            counts["review"] += int(status == "review_required")
    out = []
    for r in rows:
        base = ProductOut.model_validate(r)
        base.gallery_image_url = gallery_urls.get(r.code)
        counts = dimension_counts.get(r.code, {})
        base.dimension_asset_count = counts.get("total", 0)
        base.dimension_review_count = counts.get("review", 0)
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


class ProductDimensionSave(BaseModel):
    svg: str = Field(..., min_length=20)
    expected_version: int = Field(..., ge=1)
    size_detail: Optional[str] = Field(None, max_length=2000)
    sync_size_detail: bool = True
    confirm_mapping: bool = False


def _dimension_summary(asset: ProductDimensionAsset) -> dict:
    labels = (asset.dimension_data or {}).get("labels", [])
    return {
        "id": asset.id,
        "product_code": asset.product_code,
        "asset_key": asset.asset_key,
        "title": asset.title,
        "source_psd": asset.source_psd,
        "mapping_status": asset.mapping_status,
        "match_confidence": asset.match_confidence,
        "is_primary": asset.is_primary,
        "version": asset.version,
        "dimension_count": len(labels),
        "has_preview": bool(asset.preview_relpath),
        "updated_by": asset.updated_by,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def _dimension_detail(asset: ProductDimensionAsset, product: Product) -> dict:
    return {
        **_dimension_summary(asset),
        "product_name": product.name,
        "size_detail": product.size_detail,
        "dimension_data": asset.dimension_data or {},
        "erp_dimensions": asset.erp_dimensions or [],
        "sku_variants": asset.sku_variants or [],
        "svg_url": f"/api/products/{asset.product_code}/dimensions/{asset.id}/svg?v={asset.version}",
        "preview_url": (
            f"/api/products/{asset.product_code}/dimensions/{asset.id}/preview?v={asset.version}"
            if asset.preview_relpath else None
        ),
    }


def _product_by_code(db: Session, product_code: str) -> Product:
    product = db.execute(select(Product).where(Product.code == product_code)).scalar_one_or_none()
    if product is None:
        raise HTTPException(404, "产品不存在")
    return product


def _dimension_asset(db: Session, product_code: str, asset_id: int) -> ProductDimensionAsset:
    asset = db.get(ProductDimensionAsset, asset_id)
    if asset is None or asset.product_code != product_code:
        raise HTTPException(404, "该产品没有这张尺寸图")
    return asset


@router.get("/{product_code}/dimensions")
def list_product_dimensions(
    product_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    product = _product_by_code(db, product_code)
    rows = db.execute(
        select(ProductDimensionAsset)
        .where(ProductDimensionAsset.product_code == product_code)
        .order_by(ProductDimensionAsset.is_primary.desc(), ProductDimensionAsset.id)
    ).scalars().all()
    return {
        "product": {"code": product.code, "name": product.name, "size_detail": product.size_detail},
        "assets": [_dimension_summary(row) for row in rows],
    }


@router.get("/{product_code}/dimensions/{asset_id}")
def get_product_dimension(
    product_code: str,
    asset_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return _dimension_detail(_dimension_asset(db, product_code, asset_id), _product_by_code(db, product_code))


@router.get("/{product_code}/dimensions/{asset_id}/svg")
def get_product_dimension_svg(
    product_code: str,
    asset_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.services import product_dimension_service

    asset = _dimension_asset(db, product_code, asset_id)
    return Response(
        content=product_dimension_service.read_svg(asset.svg_relpath),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{product_code}/dimensions/{asset_id}/preview")
def get_product_dimension_preview(
    product_code: str,
    asset_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.services import product_dimension_service

    asset = _dimension_asset(db, product_code, asset_id)
    if not asset.preview_relpath:
        raise HTTPException(404, "没有预览图")
    return FileResponse(
        product_dimension_service.read_binary(asset.preview_relpath),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.put("/{product_code}/dimensions/{asset_id}")
def save_product_dimension(
    product_code: str,
    asset_id: int,
    payload: ProductDimensionSave,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保存 SVG + 结构化标签；可同一事务更新产品尺寸明细并留下修改档案。"""
    from app.services import field_change_service, product_dimension_service

    product = _product_by_code(db, product_code)
    asset = _dimension_asset(db, product_code, asset_id)
    if asset.version != payload.expected_version:
        raise HTTPException(409, f"该尺寸图已被别人更新到 v{asset.version}，请重新载入后再改")
    if asset.mapping_status == "review_required" and not payload.confirm_mapping:
        raise HTTPException(409, "这张图与产品的映射仍待核对，请先勾选确认归属")

    root = product_dimension_service.validate_and_parse_svg(payload.svg)
    metadata = product_dimension_service.merge_dimension_data(asset.dimension_data, root)
    next_version = asset.version + 1
    actor = user.display_name or user.username
    backup_rel: str | None = None
    old_size_detail = product.size_detail
    try:
        backup_rel = product_dimension_service.save_versioned_svg(
            asset.svg_relpath,
            payload.svg,
            version=asset.version,
            metadata=metadata,
        )
        field_change_service.record(
            db, table="product_dimension_assets", pk=asset.id, field="dimension_data",
            old=f"v{asset.version}", new=f"v{next_version}", actor=actor, source="web",
            row_label=f"{product.name} / {asset.title}", field_label="细节尺寸矢量图",
        )
        if payload.sync_size_detail and payload.size_detail != old_size_detail:
            field_change_service.record(
                db, table="products", pk=product.code, field="size_detail",
                old=old_size_detail, new=payload.size_detail, actor=actor, source="web",
                row_label=(product.name or product.code)[:40], field_label="尺寸明细",
            )
            product.size_detail = payload.size_detail
        asset.dimension_data = metadata
        asset.version = next_version
        asset.updated_by = actor
        if payload.confirm_mapping:
            asset.mapping_status = "confirmed"
        db.commit()
        db.refresh(asset)
        db.refresh(product)
    except Exception:
        db.rollback()
        product_dimension_service.restore_backup(asset.svg_relpath, backup_rel)
        raise
    return {**_dimension_detail(asset, product), "backup": backup_rel}


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

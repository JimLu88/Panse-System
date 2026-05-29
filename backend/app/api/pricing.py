"""定价总表 API — 读取 + 录入 + 编辑 + 成本重算."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.pricing import PricingSku
from app.services import pricing_calc_service

router = APIRouter(prefix="/api/pricing-skus", tags=["pricing"])


class PricingSkuOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_code: str
    sku: Optional[str]
    sku_code: str
    size_category: Optional[str]
    list_price: Optional[Decimal]
    daily_price: Optional[Decimal]
    small_promo: Optional[Decimal]
    mid_promo: Optional[Decimal]
    big_promo: Optional[Decimal]
    big_promo_margin: Optional[Decimal]
    gross_margin_rate: Optional[Decimal]
    accounting_cost: Optional[Decimal]
    physical_cost: Optional[Decimal]
    platform_fee_rate: Optional[Decimal]
    tax: Optional[Decimal]
    image_url: Optional[str] = None
    logistics_cost: Optional[Decimal] = None
    install_cost: Optional[Decimal] = None
    factory_cost: Optional[Decimal] = None
    wood_cost: Optional[Decimal] = None
    packaging_cost: Optional[Decimal] = None
    external_parts_cost: Optional[Decimal] = None


class PricingSkuListOut(BaseModel):
    total: int
    items: list[PricingSkuOut]


class PricingSkuIn(BaseModel):
    product_code: str
    sku_code: str
    sku: Optional[str] = None
    size_category: Optional[str] = None
    list_price: Optional[Decimal] = None
    daily_price: Optional[Decimal] = None
    small_promo: Optional[Decimal] = None
    mid_promo: Optional[Decimal] = None
    big_promo: Optional[Decimal] = None
    accounting_cost: Optional[Decimal] = None
    physical_cost: Optional[Decimal] = None
    platform_fee_rate: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    image_url: Optional[str] = None
    logistics_cost: Optional[Decimal] = None
    install_cost: Optional[Decimal] = None
    factory_cost: Optional[Decimal] = None
    wood_cost: Optional[Decimal] = None
    packaging_cost: Optional[Decimal] = None
    external_parts_cost: Optional[Decimal] = None


class PricingSkuPatch(BaseModel):
    sku: Optional[str] = None
    size_category: Optional[str] = None
    list_price: Optional[Decimal] = None
    daily_price: Optional[Decimal] = None
    small_promo: Optional[Decimal] = None
    mid_promo: Optional[Decimal] = None
    big_promo: Optional[Decimal] = None
    accounting_cost: Optional[Decimal] = None
    physical_cost: Optional[Decimal] = None
    platform_fee_rate: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    image_url: Optional[str] = None
    logistics_cost: Optional[Decimal] = None
    install_cost: Optional[Decimal] = None
    factory_cost: Optional[Decimal] = None
    wood_cost: Optional[Decimal] = None
    packaging_cost: Optional[Decimal] = None
    external_parts_cost: Optional[Decimal] = None


@router.get("", response_model=PricingSkuListOut)
def list_pricing_skus(
    q: Optional[str] = Query(None, description="按 product_code / sku_code / sku 模糊搜"),
    size_category: Optional[str] = Query(None),
    product_code: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(PricingSku)
    count_stmt = select(func.count(PricingSku.id))
    filters = []
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(
            PricingSku.product_code.ilike(like),
            PricingSku.sku_code.ilike(like),
            PricingSku.sku.ilike(like),
        ))
    if size_category:
        filters.append(PricingSku.size_category == size_category)
    if product_code:
        filters.append(PricingSku.product_code == product_code)
    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)
    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(PricingSku.product_code, PricingSku.sku_code).limit(limit).offset(offset)
    ).scalars().all()
    return PricingSkuListOut(
        total=total,
        items=[PricingSkuOut.model_validate(r) for r in rows],
    )


@router.post("", response_model=PricingSkuOut, status_code=201)
def create_pricing_sku(
    body: PricingSkuIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    existing = db.query(PricingSku).filter(PricingSku.sku_code == body.sku_code).first()
    if existing:
        raise HTTPException(400, f"sku_code '{body.sku_code}' already exists")
    sku = PricingSku(**body.model_dump(exclude_none=True))
    pricing_calc_service.recompute(sku)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return PricingSkuOut.model_validate(sku)


@router.patch("/{sku_id}", response_model=PricingSkuOut)
def update_pricing_sku(
    sku_id: int,
    body: PricingSkuPatch,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    sku = db.get(PricingSku, sku_id)
    if not sku:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(sku, k, v)
    pricing_calc_service.recompute(sku)
    db.commit()
    db.refresh(sku)
    return PricingSkuOut.model_validate(sku)


@router.post("/{sku_id}/recompute", response_model=PricingSkuOut)
def recompute_pricing_sku(
    sku_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        sku = pricing_calc_service.recompute_and_save(db, sku_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return PricingSkuOut.model_validate(sku)


# ---------------------------------------------------------------------------
# 淘宝批量操作模板下载 — 一键模板按钮用
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "taobao_templates"

# key: 文件名(不含路径), label/desc: 给前端展示
_TEMPLATES: list[dict[str, str]] = [
    {
        "key": "product_publish.xlsx",
        "label": "产品批量发布模板",
        "desc": "宝贝标题 / 商家编码 / 一口价 / SKU 价格批量发布",
    },
    {
        "key": "single_item_discount.xlsx",
        "label": "单品立减批量模板",
        "desc": "按 商品ID + SKU_ID 设置立减 / 打折优惠",
    },
    {
        "key": "promo_signup.xlsx",
        "label": "大促活动批量报名模板",
        "desc": "按商品ID报名大促 / 联报活动 (含一口价、大促价参考)",
    },
    {
        "key": "product_id_export.xlsx",
        "label": "商品ID导入模板",
        "desc": "仅商品ID一列, 用于批量勾选 / 导出商品",
    },
    {
        "key": "product_export_mapping.xlsx",
        "label": "淘宝商品导出对应表",
        "desc": "商品Id / 宝贝标题 / 商家编码 等字段对应关系",
    },
]


@router.get("/templates", tags=["pricing"])
def list_pricing_templates():
    """列出可下载的淘宝批量操作模板."""
    return [
        {"key": t["key"], "label": t["label"], "desc": t["desc"]}
        for t in _TEMPLATES
        if (_TEMPLATE_DIR / t["key"]).exists()
    ]


@router.get("/templates/{key}/download", tags=["pricing"])
def download_pricing_template(key: str):
    """下载指定模板. key 必须在白名单内, 防目录穿越."""
    meta = next((t for t in _TEMPLATES if t["key"] == key), None)
    if meta is None:
        raise HTTPException(404, "template not found")
    path = _TEMPLATE_DIR / key
    if not path.exists():
        raise HTTPException(404, "template file missing")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{meta['label']}.xlsx",
    )

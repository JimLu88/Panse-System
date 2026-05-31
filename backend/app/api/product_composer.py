"""新产品综合输入 (Task 4).

一个事务里同时创建: 产品主数据 + BOM 行 + 定价 SKU。
另提供「参考产品」加载: 把已有产品的主数据/BOM/定价拉出来给前端预填, 改改即可存为新品。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.bom import BomLine
from app.models.material import Material
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import pricing_calc_service, product_coder

router = APIRouter(prefix="/api/product-composer", tags=["product-composer"])


# ----------------------------- 输入模型 ---------------------------- #


class BomLineIn(BaseModel):
    material_code: str
    material_name: Optional[str] = None
    unit: Optional[str] = None
    qty_per_product: Decimal = Decimal("1")
    size_type: Optional[str] = None
    remark: Optional[str] = None


class PricingIn(BaseModel):
    sku_code: str = ""  # 空字符串时服务端自动分配
    is_custom: bool = False  # True → 分配 90/91… 段; False → 11/12… 段
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
    logistics_cost: Optional[Decimal] = None
    install_cost: Optional[Decimal] = None
    factory_cost: Optional[Decimal] = None
    wood_cost: Optional[Decimal] = None
    packaging_cost: Optional[Decimal] = None
    external_parts_cost: Optional[Decimal] = None


class ComposeProductIn(BaseModel):
    # 产品主数据
    name: str
    brand: str  # 2 字母
    category: str  # 2 位数字
    category_label: Optional[str] = None
    remark: Optional[str] = None
    created_on: Optional[date] = None
    taobao_id: Optional[str] = None
    bom_lines: list[BomLineIn] = []
    pricing_skus: list[PricingIn] = []


# ----------------------------- 参考加载 ---------------------------- #


class ReferenceOut(BaseModel):
    product: dict
    bom_lines: list[dict]
    pricing_skus: list[dict]


@router.get("/reference/{product_code}", response_model=ReferenceOut)
def load_reference(
    product_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """加载某个已有产品的主数据+BOM+定价, 用于「参考已有产品」预填新品表单."""
    prod = db.execute(
        select(Product).where(Product.code == product_code)
    ).scalar_one_or_none()
    if not prod:
        raise HTTPException(404, f"产品 {product_code} 不存在")

    bom = db.execute(
        select(BomLine).where(BomLine.product_code == product_code)
    ).scalars().all()
    skus = db.execute(
        select(PricingSku).where(PricingSku.product_code == product_code)
    ).scalars().all()

    return ReferenceOut(
        product={
            "code": prod.code,
            "name": prod.name,
            "brand": prod.brand,
            "category": prod.category,
            "remark": prod.remark,
        },
        bom_lines=[
            {
                "material_code": b.material_code,
                "material_name": b.material_name,
                "unit": b.unit,
                "qty_per_product": str(b.qty_per_product) if b.qty_per_product is not None else "1",
                "size_type": b.size_type,
                "remark": b.remark,
            }
            for b in bom
        ],
        pricing_skus=[
            {
                "sku_code": s.sku_code,
                "sku": s.sku,
                "size_category": s.size_category,
                "list_price": str(s.list_price) if s.list_price is not None else None,
                "daily_price": str(s.daily_price) if s.daily_price is not None else None,
                "small_promo": str(s.small_promo) if s.small_promo is not None else None,
                "mid_promo": str(s.mid_promo) if s.mid_promo is not None else None,
                "big_promo": str(s.big_promo) if s.big_promo is not None else None,
                "accounting_cost": str(s.accounting_cost) if s.accounting_cost is not None else None,
                "physical_cost": str(s.physical_cost) if s.physical_cost is not None else None,
            }
            for s in skus
        ],
    )


# ----------------------------- 综合创建 ---------------------------- #


@router.post("", response_model=dict, status_code=201)
def compose_product(
    payload: ComposeProductIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """一个事务创建 产品 + BOM + 定价. 任一步失败则全部回滚."""
    # 1) 产品编码
    try:
        code = product_coder.next_product_code(
            db, brand=payload.brand, category=payload.category,
            created_at=payload.created_on,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # 2) 校验 BOM 物料存在 (FK RESTRICT, 提前给清晰报错)
    bom_codes = {b.material_code for b in payload.bom_lines if b.material_code}
    if bom_codes:
        known = {
            m for (m,) in db.execute(
                select(Material.code).where(Material.code.in_(bom_codes))
            ).all()
        }
        missing = sorted(bom_codes - known)
        if missing:
            raise HTTPException(
                400, f"以下物料编码在物料库不存在, 请先建档: {', '.join(missing)}"
            )

    # 3) SKU 编码自动分配 (空字符串 → 按 is_custom 分配 11+/90+ 段)
    # 先算本产品已占用的 SKU 后缀
    existing_suffixes: set[int] = set()
    for (ec,) in db.execute(
        select(PricingSku.sku_code).where(PricingSku.product_code == code)
    ).all():
        if ec and ec.startswith(code):
            try:
                existing_suffixes.add(int(ec[len(code):]))
            except ValueError:
                pass
    normal_counter = 11
    custom_counter = 90
    for s in payload.pricing_skus:
        if not s.sku_code:
            if s.is_custom:
                while custom_counter in existing_suffixes or custom_counter > 99:
                    custom_counter += 1
                s.sku_code = f"{code}{custom_counter:02d}"
                existing_suffixes.add(custom_counter)
                custom_counter += 1
            else:
                while normal_counter in existing_suffixes or normal_counter >= 90:
                    normal_counter += 1
                s.sku_code = f"{code}{normal_counter:02d}"
                existing_suffixes.add(normal_counter)
                normal_counter += 1

    sku_codes = [s.sku_code for s in payload.pricing_skus if s.sku_code]
    if sku_codes:
        dup = db.execute(
            select(PricingSku.sku_code).where(PricingSku.sku_code.in_(sku_codes))
        ).scalars().all()
        if dup:
            raise HTTPException(400, f"SKU 编码已存在: {', '.join(dup)}")
        if len(set(sku_codes)) != len(sku_codes):
            raise HTTPException(400, "本次提交的 SKU 编码有重复")

    try:
        prod = Product(
            code=code,
            name=payload.name,
            brand=payload.brand.upper(),
            category=payload.category_label or payload.category,
            remark=payload.remark,
            taobao_id=payload.taobao_id,
            alt_taobao_ids=[],
        )
        db.add(prod)

        for b in payload.bom_lines:
            if not b.material_code:
                continue
            db.add(BomLine(
                product_code=code,
                product_name=payload.name,
                material_code=b.material_code,
                material_name=b.material_name,
                unit=b.unit,
                qty_per_product=b.qty_per_product or Decimal("1"),
                size_type=b.size_type,
                remark=b.remark,
            ))

        for s in payload.pricing_skus:
            if not s.sku_code:
                continue
            sku = PricingSku(
                product_code=code,
                **s.model_dump(exclude_none=True, exclude={"sku_code", "is_custom"}),
                sku_code=s.sku_code,
            )
            pricing_calc_service.recompute(sku)
            db.add(sku)

        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(400, f"创建失败已回滚: {e}")

    return {
        "product_code": code,
        "bom_lines": len(payload.bom_lines),
        "pricing_skus": len(payload.pricing_skus),
        "sku_codes": [s.sku_code for s in payload.pricing_skus if s.sku_code],
    }

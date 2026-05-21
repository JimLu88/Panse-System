"""报价服务 (plan §4)。

四个核心方法：
    - light_lookup(sku_code)                  → 直接查 pricing_sku 表，返回 4 档售价
    - high_calc(cost, size_category, margin)  → 高定大中小型，按成本 + 利润反推售价
    - any_dimension_delta(...)                → 任意尺寸差价（10 款主力，系数表待录入）
    - material_swap_delta(...)                → 换材差价
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material
from app.models.pricing import PricingSku


@dataclass
class LightQuote:
    sku_code: str
    sku: Optional[str]
    list_price: Optional[Decimal]
    daily_price: Optional[Decimal]
    small_promo: Optional[Decimal]
    mid_promo: Optional[Decimal]
    big_promo: Optional[Decimal]
    big_promo_margin: Optional[Decimal]
    gross_margin_rate: Optional[Decimal]
    size_category: Optional[str]


@dataclass
class HighQuote:
    cost: Decimal
    size_category: str
    margin_rate: Decimal
    final_price: Decimal
    margin_amount: Decimal


@dataclass
class MaterialSwapResult:
    from_code: str
    to_code: str
    qty: Decimal
    from_unit_price: Optional[Decimal]
    to_unit_price: Optional[Decimal]
    delta: Optional[Decimal]  # 正数 = 涨价（B 比 A 贵）


# 高定大小型默认利润率 (plan §4：高定大中小型 + 15%/25% 利润)
HIGH_CUSTOM_MARGIN_BY_SIZE: dict[str, Decimal] = {
    "小型": Decimal("0.15"),
    "中型": Decimal("0.15"),
    "大型": Decimal("0.25"),
}


def light_lookup(db: Session, sku_code: str) -> Optional[LightQuote]:
    """轻定制四档售价：直接查 pricing_sku 表。"""
    row = db.execute(
        select(PricingSku).where(PricingSku.sku_code == sku_code)
    ).scalar_one_or_none()
    if row is None:
        return None
    return LightQuote(
        sku_code=row.sku_code,
        sku=row.sku,
        list_price=row.list_price,
        daily_price=row.daily_price,
        small_promo=row.small_promo,
        mid_promo=row.mid_promo,
        big_promo=row.big_promo,
        big_promo_margin=row.big_promo_margin,
        gross_margin_rate=row.gross_margin_rate,
        size_category=row.size_category,
    )


def high_calc(
    *,
    cost: Decimal,
    size_category: str,
    margin_rate: Optional[Decimal] = None,
) -> HighQuote:
    """高定大中小型：售价 = 成本 / (1 - 利润率)。

    若不传 margin_rate，则按 size_category 取默认 (小/中 15% / 大 25%)。
    """
    if cost <= 0:
        raise ValueError("cost must be > 0")
    if margin_rate is None:
        margin_rate = HIGH_CUSTOM_MARGIN_BY_SIZE.get(size_category)
    if margin_rate is None:
        raise ValueError(
            f"未知 size_category={size_category!r} 且未传 margin_rate；"
            f"已知值: {list(HIGH_CUSTOM_MARGIN_BY_SIZE)}"
        )
    if margin_rate >= 1 or margin_rate < 0:
        raise ValueError("margin_rate must be in [0, 1)")
    final = (cost / (Decimal("1") - margin_rate)).quantize(Decimal("0.01"))
    return HighQuote(
        cost=cost,
        size_category=size_category,
        margin_rate=margin_rate,
        final_price=final,
        margin_amount=(final - cost).quantize(Decimal("0.01")),
    )


def material_swap_delta(
    db: Session,
    *,
    from_code: str,
    to_code: str,
    qty: Decimal = Decimal("1"),
) -> MaterialSwapResult:
    """换材差价：(to.price - from.price) × qty。

    任一边价格缺失时，delta 返回 None（异常处理页提示先补价）。
    """
    if from_code == to_code:
        return MaterialSwapResult(
            from_code=from_code, to_code=to_code, qty=qty,
            from_unit_price=None, to_unit_price=None, delta=Decimal("0"),
        )
    mats = {m.code: m for m in db.execute(
        select(Material).where(Material.code.in_([from_code, to_code]))
    ).scalars()}
    a = mats.get(from_code)
    b = mats.get(to_code)
    if a is None or b is None:
        missing = [c for c, m in [(from_code, a), (to_code, b)] if m is None]
        raise ValueError(f"materials not found: {missing}")

    if a.price is None or b.price is None:
        return MaterialSwapResult(
            from_code=from_code, to_code=to_code, qty=qty,
            from_unit_price=a.price, to_unit_price=b.price, delta=None,
        )
    delta = ((b.price - a.price) * qty).quantize(Decimal("0.01"))
    return MaterialSwapResult(
        from_code=from_code, to_code=to_code, qty=qty,
        from_unit_price=a.price, to_unit_price=b.price, delta=delta,
    )


def any_dimension_delta(
    *,
    base_cm: Decimal,
    target_cm: Decimal,
    per_cm_cost: Decimal,
    margin_rate: Decimal = Decimal("0.15"),
) -> Decimal:
    """任意尺寸差价：(target - base) × per_cm × (1 + margin)。

    per_cm_cost 是「每多 1cm 多耗的成本」，目前没建系数表，调用方需自带。
    plan §11 列为待澄清项之一。
    """
    if per_cm_cost < 0:
        raise ValueError("per_cm_cost must be >= 0")
    if margin_rate >= 1 or margin_rate < 0:
        raise ValueError("margin_rate must be in [0, 1)")
    cm_diff = target_cm - base_cm
    delta = cm_diff * per_cm_cost * (Decimal("1") + margin_rate)
    return delta.quantize(Decimal("0.01"))

"""定价计算工具 — 成本/毛利率重算。

公式:
  gross_margin_rate = (售价 − accounting_cost − tax − platform_fee_amount) / 售价
  big_promo_margin  = big_promo × (1 - platform_fee_rate) − accounting_cost − tax
  platform_fee_amount = 售价 × platform_fee_rate
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo


def _d(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def recompute(sku: PricingSku) -> None:
    """原地重算毛利率和大促利润（仅当相关成本字段有值）。"""
    cost = _d(sku.accounting_cost)
    tax = _d(sku.tax) or Decimal("0")
    pfr = _d(sku.platform_fee_rate) or Decimal("0")

    def _margin(price_val):
        if price_val is None or cost is None:
            return None
        price = _d(price_val)
        pf = (price * pfr).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        margin = (price - cost - tax - pf) / price
        return margin.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    if sku.daily_price:
        sku.gross_margin_rate = _margin(sku.daily_price)

    if sku.big_promo and cost is not None:
        big = _d(sku.big_promo)
        pf = (big * pfr).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        sku.big_promo_margin = (big - pf - cost - tax).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def recompute_and_save(db: Session, sku_id: int) -> PricingSku:
    sku = db.get(PricingSku, sku_id)
    if not sku:
        raise ValueError(f"PricingSku {sku_id} not found")
    recompute(sku)
    db.commit()
    db.refresh(sku)
    return sku


def recompute_promo(promo: PricingSkuPromo, sku: PricingSku) -> None:
    """按公式重算活动价各派生字段. promo 和 sku 对象直接 mutate."""
    from decimal import Decimal as D, ROUND_HALF_UP
    daily = sku.daily_price
    if daily is None:
        return
    promo.taobao_activity_price = daily
    promo.xhs_list_price = daily
    # 小促
    if promo.shop_promo_rate and daily:
        promo.shop_internal_final = (daily * promo.shop_promo_rate).quantize(D("0.01"), ROUND_HALF_UP)
    # 无国补中促
    if promo.mid_shop_rate and daily:
        mid = (daily * D("0.88") * promo.mid_shop_rate).quantize(D("0.01"), ROUND_HALF_UP)
        promo.mid_buyer_price = mid
        promo.mid_shop_receipt = (mid * D("0.99")).quantize(D("0.01"), ROUND_HALF_UP)
        promo.mid_vip_final = mid - D("150")
    # 无国补大促
    if promo.big_shop_rate and daily:
        big = (daily * D("0.88") * promo.big_shop_rate).quantize(D("0.01"), ROUND_HALF_UP)
        promo.big_buyer_price = big
        promo.big_shop_receipt = big
        promo.big_vip_final = big - D("150")
    # 小红书
    if promo.xhs_activity_price:
        discount = promo.xhs_promo_discount or D("0.15")
        promo.xhs_promo_price = (promo.xhs_activity_price * (D("1") - discount)).quantize(D("0.01"), ROUND_HALF_UP)


def recompute_costs(costs: PricingSkuCosts, sku: PricingSku) -> None:
    """根据 22 项配件成本重算 sku.external_parts_cost (sum of all non-None cost fields)."""
    from decimal import Decimal as D
    COST_FIELDS = [
        "rock_slab","drawer_rail","led_strip","glass","electric_rail","packing_sheet",
        "iron_pin","connector","aluminum_rail","plastic_rail","mini_handle","nail_free_glue",
        "engraving","acrylic_strip","embedded_sleeve","cable_mgmt","back_panel","stainless_trim",
        "leg","soft_pack","bed_board","other_cost",
    ]
    total = sum((getattr(costs, f) or D("0")) for f in COST_FIELDS)
    sku.external_parts_cost = total if total > 0 else None

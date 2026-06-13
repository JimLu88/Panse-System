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
    """原地按【定价总表口径】重算 平台费/税/会计成本/大促利润/毛利率。

    依赖 大促价(K) 与 物理成本(Q):
      平台费 O = 大促价 × 0.6% ; 税 P = 大促价 × 2%
      会计成本 N = 物理成本 + 平台费 + 税
      大促利润 L = 大促价 − 会计成本 ; 毛利率 M = 大促利润 ÷ 大促价
    只让利润链跟随, 不动 物流/安装/出厂/物理 等成本输入(可手填/按SKU调整)。
    """
    cent = Decimal("0.01")
    big = _d(sku.big_promo)
    phys = _d(sku.physical_cost)
    if big is not None:
        sku.platform_fee_rate = (big * Decimal("0.006")).quantize(cent, rounding=ROUND_HALF_UP)
        sku.tax = (big * Decimal("0.02")).quantize(cent, rounding=ROUND_HALF_UP)
    pf = _d(sku.platform_fee_rate) or Decimal("0")
    tax = _d(sku.tax) or Decimal("0")
    if phys is not None:
        sku.accounting_cost = (phys + pf + tax).quantize(cent, rounding=ROUND_HALF_UP)
    cost = _d(sku.accounting_cost)
    if big is not None and big != 0 and cost is not None:
        margin = (big - cost).quantize(cent, rounding=ROUND_HALF_UP)
        sku.big_promo_margin = margin
        sku.gross_margin_rate = (margin / big).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


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

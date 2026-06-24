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


# 活动价全局参数(按档)默认值 —— 复刻改造前口径: 平台立减(力度)12%; 88VIP佣金 中促1%/大促0%。
# 以此为默认时, 下面的到手价/店铺到账/会员价与改造前【完全一致】(核对吻合用)。
PROMO_PARAM_DEFAULTS = {
    "mid_platform_discount": "0.12", "mid_vip_commission": "0.01",
    "big_platform_discount": "0.12", "big_vip_commission": "0.00",
}
# 88VIP 消费券阶梯默认 (来自用户活动报名表): 到手价 ≥阈值 → 减额。降序匹配, 取满足的最高一档。
COUPON_TIERS_DEFAULT = [[1500, 150], [800, 80], [500, 50], [200, 20]]


def _coupon_deduction(amount, tiers):
    """按消费券阶梯求减额: 取「阈值 ≤ 到手价」中阈值最高那档的减额; 都不满足=0。"""
    from decimal import Decimal as D
    best = D("0")
    for thr, ded in sorted(tiers, key=lambda x: x[0], reverse=True):
        if amount >= thr:
            return D(str(ded))
    return best


def get_promo_params(db) -> dict:
    """读活动价全局参数(平台立减/88VIP佣金/消费券阶梯, 按中促/大促分档), 存 system_settings;
    没配过 → 用默认(平台立减/佣金=改造前口径; 消费券阶梯=活动表口径)。"""
    from decimal import Decimal as D
    import json
    from app.services import settings_service
    out: dict = {}
    for k, dflt in PROMO_PARAM_DEFAULTS.items():
        raw = settings_service.get(db, f"promo_{k}", env_fallback=False)
        try:
            out[k] = D(str(raw)) if raw not in (None, "") else D(dflt)
        except Exception:
            out[k] = D(dflt)
    # 消费券阶梯 (按档), 存 JSON: [[阈值, 减额], ...]
    for tier_key in ("mid_coupon_tiers", "big_coupon_tiers"):
        raw = settings_service.get(db, f"promo_{tier_key}", env_fallback=False)
        tiers = None
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    tiers = [[D(str(a)), D(str(b))] for a, b in parsed]
            except Exception:
                tiers = None
        out[tier_key] = tiers or [[D(str(a)), D(str(b))] for a, b in COUPON_TIERS_DEFAULT]
    return out


def recompute_promo(promo: PricingSkuPromo, sku: PricingSku, params: Optional[dict] = None) -> None:
    """按公式重算活动价各派生字段. promo 和 sku 对象直接 mutate.
    params: 活动价全局参数(平台立减/88VIP佣金, 按档); 不传 → PROMO_PARAM_DEFAULTS(=改造前口径)。
    口径: 到手 = 日常 ×(1−平台立减)× 店铺宝系数; 店铺到账 = 到手 ×(1−88VIP佣金); 会员价 = 到手 −150。"""
    from decimal import Decimal as D, ROUND_HALF_UP
    daily = sku.daily_price
    if daily is None:
        return
    p = params or {k: D(v) for k, v in PROMO_PARAM_DEFAULTS.items()}
    mid_disc = D(str(p.get("mid_platform_discount", PROMO_PARAM_DEFAULTS["mid_platform_discount"])))
    mid_comm = D(str(p.get("mid_vip_commission", PROMO_PARAM_DEFAULTS["mid_vip_commission"])))
    big_disc = D(str(p.get("big_platform_discount", PROMO_PARAM_DEFAULTS["big_platform_discount"])))
    big_comm = D(str(p.get("big_vip_commission", PROMO_PARAM_DEFAULTS["big_vip_commission"])))
    mid_tiers = p.get("mid_coupon_tiers") or [[D(str(a)), D(str(b))] for a, b in COUPON_TIERS_DEFAULT]
    big_tiers = p.get("big_coupon_tiers") or [[D(str(a)), D(str(b))] for a, b in COUPON_TIERS_DEFAULT]
    promo.taobao_activity_price = daily
    promo.xhs_list_price = daily
    # 小促
    if promo.shop_promo_rate and daily:
        promo.shop_internal_final = (daily * promo.shop_promo_rate).quantize(D("0.01"), ROUND_HALF_UP)
    # 无国补中促
    if promo.mid_shop_rate and daily:
        mid = (daily * (D("1") - mid_disc) * promo.mid_shop_rate).quantize(D("0.01"), ROUND_HALF_UP)
        promo.mid_buyer_price = mid
        promo.mid_shop_receipt = (mid * (D("1") - mid_comm)).quantize(D("0.01"), ROUND_HALF_UP)
        promo.mid_vip_final = mid - _coupon_deduction(mid, mid_tiers)
        promo.mid_platform_discount = mid_disc      # 记录所用力度/佣金, 供前端单列展示
        promo.mid_vip_commission = mid_comm
    # 无国补大促
    if promo.big_shop_rate and daily:
        big = (daily * (D("1") - big_disc) * promo.big_shop_rate).quantize(D("0.01"), ROUND_HALF_UP)
        promo.big_buyer_price = big
        promo.big_shop_receipt = (big * (D("1") - big_comm)).quantize(D("0.01"), ROUND_HALF_UP)
        promo.big_vip_final = big - _coupon_deduction(big, big_tiers)
        promo.big_platform_discount = big_disc
        promo.big_vip_commission = big_comm
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

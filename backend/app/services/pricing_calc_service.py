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

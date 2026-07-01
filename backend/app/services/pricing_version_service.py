"""定价版本历史服务 (工厂调价历史: 老单老价/新单新价, 历史利润不追溯改写)。

- record_dated_change(sku, D): 调价"从 D 起生效"时, 把**改前(旧)**值快照成已关闭区间 [上个边界或起点, D)。
  调用时机: 在把新值写进 sku 之前 (先快照旧值)。之后 caller 照常写新值 + recompute。
- values_at(sku_code, product_code, on_date): 订单按 order_date 取生效版本; on_date 落在最后边界之后
  或该 SKU 无任何版本 → 返回 None (由 caller 回退 live pricing_sku = 改造前行为)。
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.pricing_version import SENTINEL_START, PricingSkuVersion

# 快照进版本行的定价值字段 (成本+售价+基数); 前段做真实列, 其余进 snapshot JSON。
_COL_FIELDS = [
    "physical_cost", "factory_cost", "logistics_cost", "install_cost",
    "wood_cost", "external_parts_cost", "packaging_cost",
    "list_price", "daily_price", "small_promo", "mid_promo", "big_promo",
]
_JSON_FIELDS = [
    "accounting_cost", "platform_fee_rate", "tax", "big_promo_margin",
    "gross_margin_rate", "base_list", "base_small", "base_mid", "base_big",
]


def _last_end(db: Session, sku_code: str) -> Optional[date]:
    return db.execute(
        select(PricingSkuVersion.period_end)
        .where(PricingSkuVersion.sku_code == sku_code)
        .order_by(PricingSkuVersion.period_end.desc()).limit(1)
    ).scalar_one_or_none()


def record_dated_change(db: Session, sku: PricingSku, effective_from: date, *,
                        actor: Optional[str] = None, note: Optional[str] = None) -> PricingSkuVersion:
    """把 sku 的**当前(旧)**定价值封存成一条区间 [上个边界或起点, effective_from)。调用须早于写新值。

    区间连续: period_start = 上一次调价边界 (无则 SENTINEL_START)。
    生效日必须晚于上个边界 (否则区间倒挂) → 抛 ValueError。
    """
    prev = _last_end(db, sku.sku_code)
    start = prev or SENTINEL_START
    if effective_from <= start:
        raise ValueError(f"生效日 {effective_from} 必须晚于上一次调价边界 {start}")
    snap = {f: (str(getattr(sku, f)) if getattr(sku, f, None) is not None else None)
            for f in (_COL_FIELDS + _JSON_FIELDS)}
    row = PricingSkuVersion(
        sku_code=sku.sku_code, product_code=sku.product_code,
        period_start=start, period_end=effective_from,
        snapshot=json.dumps(snap, ensure_ascii=True),
        note=note, created_by=actor,
        **{f: getattr(sku, f, None) for f in _COL_FIELDS},
    )
    db.add(row)
    return row


def values_at(db: Session, *, sku_code: Optional[str], product_code: Optional[str],
              on_date: Optional[date]) -> Optional[PricingSkuVersion]:
    """返回 order_date 生效的版本行; None = 用 live pricing_sku (无版本 / 落在最后边界之后)。"""
    if on_date is None:
        return None
    rows = []
    if sku_code:
        rows = db.execute(
            select(PricingSkuVersion).where(PricingSkuVersion.sku_code == sku_code)
            .order_by(PricingSkuVersion.period_start)
        ).scalars().all()
    if not rows and product_code:
        rows = db.execute(
            select(PricingSkuVersion).where(PricingSkuVersion.product_code == product_code)
            .order_by(PricingSkuVersion.period_start)
        ).scalars().all()
    if not rows:
        return None
    last_end = max(r.period_end for r in rows)
    if on_date >= last_end:
        return None                      # 落在当前开区间 → 用 live
    for r in rows:
        if r.period_start <= on_date < r.period_end:
            return r
    # 早于最早区间起点 (理论不会, 起点=SENTINEL) → 用最早一版
    return min(rows, key=lambda r: r.period_start)


def physical_at(db: Session, *, sku_code: Optional[str], product_code: Optional[str],
                on_date: Optional[date]) -> Optional[Decimal]:
    """order_date 生效的物理总成本; None = 无适用版本(caller 回退 live)。"""
    v = values_at(db, sku_code=sku_code, product_code=product_code, on_date=on_date)
    if v is None:
        return None
    if v.physical_cost is not None:
        return Decimal(str(v.physical_cost))
    if v.factory_cost is not None:      # physical 缺 → 出厂+物流+安装 (与 _pricing_cost_for 回退一致)
        return (Decimal(str(v.factory_cost)) + Decimal(str(v.logistics_cost or 0))
                + Decimal(str(v.install_cost or 0)))
    return None

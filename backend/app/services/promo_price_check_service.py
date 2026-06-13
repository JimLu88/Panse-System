"""活动报名价 ↔ 定价渠道价 自动对照 (Plan F1)。

报名价 (campaign_signup_prices, 导入/OCR) vs 定价表渠道活动价
(PricingSkuPromo.taobao_activity_price / xhs_activity_price)。
超 system_settings.promo_price_tolerance (元, 默认 1) →
DataException(promo_price_mismatch) + critical alert (dedupe promo_price:{sku}:{channel})。

调度: daily_0830_promo_price_check。只提示, 不自动改价。
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign_signup import CampaignSignupPrice
from app.models.pricing_ext import PricingSkuPromo

_logger = logging.getLogger("panse.promo_price_check")

_DEFAULT_TOLERANCE = Decimal("1.0")

# 渠道 → 定价表对照字段
_CHANNEL_FIELD = {
    "taobao": "taobao_activity_price",
    "xhs": "xhs_activity_price",
}


def _tolerance(db: Session) -> Decimal:
    try:
        from app.services import settings_service
        raw = settings_service.get(db, "promo_price_tolerance", env_fallback=False)
        return Decimal(str(raw)) if raw else _DEFAULT_TOLERANCE
    except Exception:  # pragma: no cover
        return _DEFAULT_TOLERANCE


def check_one(db: Session, row: CampaignSignupPrice, *,
              tolerance: Optional[Decimal] = None) -> dict:
    """单条报名价对照。返回 {sku_code, channel, mismatch, signup, pricing, diff}。"""
    if tolerance is None:
        tolerance = _tolerance(db)
    field = _CHANNEL_FIELD.get(row.channel)
    if field is None:
        return {"sku_code": row.sku_code, "channel": row.channel,
                "mismatch": False, "skipped": f"未知渠道 {row.channel}"}
    promo = db.execute(
        select(PricingSkuPromo).where(PricingSkuPromo.sku_code == row.sku_code)
    ).scalar_one_or_none()
    pricing_price = getattr(promo, field, None) if promo is not None else None
    if pricing_price is None:
        return {"sku_code": row.sku_code, "channel": row.channel,
                "mismatch": False, "skipped": "定价表无该渠道活动价"}
    diff = (Decimal(row.signup_price) - Decimal(pricing_price)).quantize(Decimal("0.01"))
    if abs(diff) <= tolerance:
        return {"sku_code": row.sku_code, "channel": row.channel,
                "mismatch": False, "diff": float(diff)}
    desc = (f"{row.sku_code} [{row.channel}] 活动报名价 {row.signup_price} vs "
            f"定价表渠道价 {pricing_price} (差 {diff}, 活动 {row.campaign_name or '—'})。"
            f"报错价卖一单亏一单, 请核对报名后台或修正定价表。")
    try:
        from app.services import exception_service
        exception_service.record(
            db, source_table="campaign_signup_prices", source_pk=str(row.id),
            exception_type="promo_price_mismatch", severity="warning",
            description=desc, suggestion_action="check_campaign_signup",
        )
    except Exception:  # pragma: no cover
        _logger.warning("promo_price_mismatch 异常写入失败", exc_info=True)
    try:
        from app.services import alert_service
        alert_service.upsert(
            db, kind="promo_price_mismatch", severity="critical",
            title=f"活动价不符: {row.sku_code} [{row.channel}]",
            body=desc,
            dedupe_key=f"promo_price:{row.sku_code}:{row.channel}",
            related_url="/pricing",
            context={"sku_code": row.sku_code, "channel": row.channel,
                     "signup_price": float(row.signup_price),
                     "pricing_price": float(pricing_price), "diff": float(diff)},
            sticky=True,
        )
    except Exception:  # pragma: no cover
        _logger.warning("promo_price alert 写入失败", exc_info=True)
    return {"sku_code": row.sku_code, "channel": row.channel, "mismatch": True,
            "signup": float(row.signup_price), "pricing": float(pricing_price),
            "diff": float(diff)}


def check_all(db: Session) -> dict:
    """全量对照所有报名价。"""
    rows = db.execute(select(CampaignSignupPrice)).scalars().all()
    tol = _tolerance(db)
    mismatches = []
    checked = 0
    for r in rows:
        out = check_one(db, r, tolerance=tol)
        if out.get("skipped"):
            continue
        checked += 1
        if out["mismatch"]:
            mismatches.append(out)
    db.flush()
    return {"checked": checked, "mismatch_count": len(mismatches),
            "mismatches": mismatches[:100], "tolerance": float(tol)}

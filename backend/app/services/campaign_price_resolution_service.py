"""Deterministic price-conflict resolution before any campaign write.

The resolver is deliberately pure.  It never changes ERP or Taobao; callers
persist the returned decision in the campaign audit before a platform write.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP


CENT = Decimal("0.01")
CUSTOM_MIN_FINAL_RATIO = Decimal("0.20")


def _d(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, ROUND_HALF_UP)


def resolve(
        *, daily_price, target_final_price, official_rate,
        official_ceil_to_yuan=True,
        minimum_list_price=None, minimum_coupon_after_price=None,
        is_custom=False, immutable_baseline_daily_price=None,
        authorized_concession=0) -> dict:
    """Return an auditable safe resolution without mutating ERP or Taobao."""
    daily = _d(daily_price)
    target = _d(target_final_price)
    authorized = _d(authorized_concession)
    # Real SKU campaign price is an invariant, not a negotiable optimization.
    # Platform history may make an item ineligible, but it may never rewrite
    # the ERP daily price or trigger an SKU-identity rotation here.
    signup = daily
    reasons: list[dict] = []

    if minimum_list_price is not None:
        line = _d(minimum_list_price)
        gap = max(Decimal("0"), daily - line)
        if gap and not is_custom:
            return {
                "ok": False,
                "action": "hold_whole_item",
                "reason": "real_sku_signup_price_would_differ_from_erp_daily",
                "required_signup_reduction": float(gap),
                "signup_price": float(daily),
                "minimum_list_price": float(line),
                "reasons": [{"type": "minimum_list_price", "amount": float(gap)}],
            }
        if gap:
            signup -= gap
            reasons.append({"type": "minimum_list_price", "amount": float(gap)})

    raw_official = signup * Decimal(str(official_rate))
    official = (
        raw_official.to_integral_value(rounding=ROUND_CEILING)
        if official_ceil_to_yuan and signup >= Decimal("100")
        else raw_official.quantize(CENT, ROUND_HALF_UP)
    )

    # The ordinary single-item discount lands on the ERP target.  A lower
    # platform coupon line is an *additional* concession, never a replacement
    # for the ERP target calculation.
    authorized_target = (target - authorized).quantize(CENT)
    normal_single = max(Decimal("0"), signup - official - authorized_target)
    final = (signup - official - normal_single).quantize(CENT)
    coupon_concession = Decimal("0")
    if minimum_coupon_after_price is not None:
        line = _d(minimum_coupon_after_price)
        required = max(Decimal("0"), final - line)
        if required and not is_custom:
            return {
                "ok": False,
                "action": "hold_whole_item",
                "reason": "erp_target_above_platform_coupon_floor",
                "required_coupon_concession": float(required),
                "signup_price": float(daily),
                "erp_target_price": float(target),
                "authorized_target_price": float(final),
                "minimum_coupon_after_price": float(line),
                "reasons": [{
                    "type": "minimum_coupon_after_price",
                    "amount": float(required),
                }],
            }
        coupon_concession = required
        if required:
            reasons.append({
                "type": "minimum_coupon_after_price",
                "amount": float(required),
            })
    single = (normal_single + coupon_concession).quantize(CENT)
    final = (signup - official - single).quantize(CENT)
    signup_concession = (daily - signup).quantize(CENT)
    total_concession = (
        signup_concession + authorized + coupon_concession
    ).quantize(CENT)
    if is_custom:
        if immutable_baseline_daily_price is None:
            return {"ok": False, "action": "block", "reason": "missing_custom_baseline"}
        floor = (_d(immutable_baseline_daily_price) * CUSTOM_MIN_FINAL_RATIO).quantize(
            CENT, ROUND_HALF_UP)
        if final < floor:
            return {
                "ok": False,
                "action": "block",
                "reason": "custom_final_below_20_percent_baseline",
                "final_price": float(final),
                "minimum_final_price": float(floor),
                "reasons": reasons,
            }

    return {
        "ok": True,
        "action": "adjust" if is_custom and (reasons or authorized > 0) else "unchanged",
        "signup_price": float(signup),
        "official_deduction": float(official),
        "single_discount": float(single),
        "final_price": float(final),
        "signup_concession": float(signup_concession),
        "coupon_concession": float(coupon_concession),
        "authorized_concession": float(authorized),
        "total_concession": float(total_concession),
        "reasons": reasons,
    }

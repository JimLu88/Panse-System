"""售后金额统一口径。

平台外总额已填时它是权威总数；历史行没填总额时，再由明细字段补足。
这能保证售后页、数据大盘和财务概览显示同一个数。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case, func

from app.models.marketing import AfterSales


EXTRA_FIELDS = (
    "direct_compensation", "second_visit_fee", "return_pack_freight",
    "refill_freight", "wanshifu_deduction", "good_review_refund",
)


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def out_platform_breakdown(row: AfterSales) -> Decimal:
    return sum((_d(getattr(row, field, None)) for field in EXTRA_FIELDS), Decimal("0"))


def total_cost(row: AfterSales) -> Decimal:
    outside = _d(row.out_platform_total) if row.out_platform_total is not None else out_platform_breakdown(row)
    return _d(row.in_platform_total) + outside


def total_cost_expr():
    breakdown = sum((func.coalesce(getattr(AfterSales, field), 0) for field in EXTRA_FIELDS))
    outside = case(
        (AfterSales.out_platform_total.isnot(None), AfterSales.out_platform_total),
        else_=breakdown,
    )
    return func.coalesce(AfterSales.in_platform_total, 0) + func.coalesce(outside, 0)

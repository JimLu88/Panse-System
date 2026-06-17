"""推广 ROI 计算 (plan §10 Phase 5 推广记录 ROI).

简化模型：
    ROI = (期间内订单实付总额 - 期间内推广支出) / 推广支出
    转化率 = 订单数 / (期间内推广支出 ÷ 平均客单价)  ← 近似
真实电商有归因模型，这里只算粗 ROI 供日常巡检用。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.marketing import PromotionFlow
from app.models.order import Order


@dataclass
class RoiReport:
    period_start: Optional[date]
    period_end: Optional[date]
    promotion_spend: Decimal
    promotion_recharge: Decimal
    order_count: int
    order_revenue: Decimal
    avg_order_value: Decimal
    roi: Optional[Decimal]  # None when spend=0


def compute(
    db: Session,
    *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> RoiReport:
    spend_q = select(func.coalesce(func.sum(PromotionFlow.amount), 0)).where(
        PromotionFlow.flow_type == "支出"
    )
    recharge_q = select(func.coalesce(func.sum(PromotionFlow.amount), 0)).where(
        PromotionFlow.flow_type == "充值"
    )
    from app.services.sales_analytics import settled_sale_clause
    order_revenue_q = select(
        func.coalesce(func.sum(Order.paid_amount), 0).label("rev"),
        func.count(Order.id).label("cnt"),
    ).where(settled_sale_clause(), Order.is_refill == False)  # noqa: E712 真实成交·剔除补单 (2026-06-17)

    if period_start:
        spend_q = spend_q.where(PromotionFlow.transaction_date >= period_start)
        recharge_q = recharge_q.where(PromotionFlow.transaction_date >= period_start)
        order_revenue_q = order_revenue_q.where(Order.order_date >= period_start)
    if period_end:
        spend_q = spend_q.where(PromotionFlow.transaction_date <= period_end)
        recharge_q = recharge_q.where(PromotionFlow.transaction_date <= period_end)
        order_revenue_q = order_revenue_q.where(Order.order_date <= period_end)

    spend = Decimal(db.execute(spend_q).scalar() or 0)
    recharge = Decimal(db.execute(recharge_q).scalar() or 0)
    rev, cnt = db.execute(order_revenue_q).one()
    rev = Decimal(rev or 0)
    cnt = int(cnt or 0)
    aov = (rev / cnt).quantize(Decimal("0.01")) if cnt > 0 else Decimal("0")
    roi = ((rev - spend) / spend).quantize(Decimal("0.0001")) if spend > 0 else None

    return RoiReport(
        period_start=period_start,
        period_end=period_end,
        promotion_spend=spend,
        promotion_recharge=recharge,
        order_count=cnt,
        order_revenue=rev,
        avg_order_value=aov,
        roi=roi,
    )


def monthly_breakdown(db: Session, *, year: Optional[int] = None) -> dict:
    """按月：推广支出 占 正式销售额(剔除补单) 的占比 + ROI。

    占比 spend_ratio = 推广支出 / 正式销售额 (花了销售额的百分之几在推广)。
    口径与销售排行榜一致：正式销售 = 非补单、非取消、有下单日；销售额 = 买家实付。
    返回 {months[降序], total_spend, total_revenue, total_order_count, overall_spend_ratio}。
    """
    spend_rows = db.execute(
        select(PromotionFlow.transaction_date, PromotionFlow.amount).where(
            PromotionFlow.flow_type == "支出",
            PromotionFlow.transaction_date.isnot(None),
        )
    ).all()
    order_rows = db.execute(
        select(Order.order_date, Order.paid_amount).where(
            Order.is_refill == False,  # noqa: E712
            Order.status != "cancelled",
            Order.order_date.isnot(None),
        )
    ).all()

    spend_by: dict[str, Decimal] = {}
    for d, amt in spend_rows:
        if year and d.year != year:
            continue
        k = f"{d.year:04d}-{d.month:02d}"
        spend_by[k] = spend_by.get(k, Decimal("0")) + Decimal(amt or 0)

    rev_by: dict[str, Decimal] = {}
    cnt_by: dict[str, int] = {}
    for d, paid in order_rows:
        if year and d.year != year:
            continue
        k = f"{d.year:04d}-{d.month:02d}"
        rev_by[k] = rev_by.get(k, Decimal("0")) + Decimal(paid or 0)
        cnt_by[k] = cnt_by.get(k, 0) + 1

    months: list[dict] = []
    tot_spend = tot_rev = Decimal("0")
    tot_cnt = 0
    for k in sorted(set(spend_by) | set(rev_by), reverse=True):
        spend = spend_by.get(k, Decimal("0"))
        rev = rev_by.get(k, Decimal("0"))
        cnt = cnt_by.get(k, 0)
        ratio = float(spend / rev) if rev > 0 else None
        roi = float(((rev - spend) / spend).quantize(Decimal("0.0001"))) if spend > 0 else None
        months.append({
            "period": k,
            "promotion_spend": float(spend),
            "order_revenue": float(rev),
            "order_count": cnt,
            "spend_ratio": round(ratio, 4) if ratio is not None else None,
            "roi": roi,
        })
        tot_spend += spend
        tot_rev += rev
        tot_cnt += cnt

    overall = float(tot_spend / tot_rev) if tot_rev > 0 else None
    return {
        "months": months,
        "total_spend": float(tot_spend),
        "total_revenue": float(tot_rev),
        "total_order_count": tot_cnt,
        "overall_spend_ratio": round(overall, 4) if overall is not None else None,
    }

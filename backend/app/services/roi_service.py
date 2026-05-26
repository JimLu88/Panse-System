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
    order_revenue_q = select(
        func.coalesce(func.sum(Order.paid_amount), 0).label("rev"),
        func.count(Order.id).label("cnt"),
    ).where(Order.status != "cancelled")

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

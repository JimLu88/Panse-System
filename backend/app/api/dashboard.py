"""Dashboard 聚合 API — 订单/库存/财务三大指标."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.order import Order
from app.models.inventory import PartInventory, ProductInventory
from app.models.finance import AlipayFlow
from app.models.exception import DataException

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _safe_decimal(v) -> float:
    if v is None:
        return 0.0
    return float(Decimal(str(v)))


@router.get("")
def get_dashboard(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    today = date.today()
    last_30 = today - timedelta(days=30)
    last_7 = today - timedelta(days=7)

    # ── 订单概览 ──────────────────────────────────────────────
    status_counts = dict(
        db.query(Order.status, func.count(Order.id))
        .filter(Order.is_historical == False)  # noqa: E712
        .group_by(Order.status)
        .all()
    )

    # 近 30 天趋势 (按周分组)
    trend_rows = (
        db.query(Order.order_date, func.count(Order.id), func.sum(Order.paid_amount))
        .filter(Order.order_date >= last_30, Order.is_historical == False)  # noqa: E712
        .group_by(Order.order_date)
        .order_by(Order.order_date)
        .all()
    )
    order_trend = [
        {"date": str(r[0]), "count": r[1], "revenue": _safe_decimal(r[2])}
        for r in trend_rows if r[0]
    ]

    total_orders_30d = sum(r["count"] for r in order_trend)
    total_revenue_30d = sum(r["revenue"] for r in order_trend)

    orders_7d = (
        db.query(func.count(Order.id))
        .filter(Order.order_date >= last_7, Order.is_historical == False)  # noqa: E712
        .scalar() or 0
    )

    # ── 库存运营 ──────────────────────────────────────────────
    part_total = db.query(func.count(PartInventory.id)).scalar() or 0
    part_negative = (
        db.query(func.count(PartInventory.id))
        .filter(PartInventory.physical_qty < 0)
        .scalar() or 0
    )

    prod_total = db.query(func.count(ProductInventory.id)).scalar() or 0
    prod_low = (
        db.query(func.count(ProductInventory.id))
        .filter(ProductInventory.qty <= 5)
        .scalar() or 0
    )

    # ── 财务概览 ──────────────────────────────────────────────
    revenue_30d_alipay = (
        db.query(func.sum(AlipayFlow.amount))
        .filter(
            AlipayFlow.amount > 0,
            AlipayFlow.transaction_time >= func.date(str(last_30)),
        )
        .scalar()
    )

    # 未解决异常数
    open_exceptions = (
        db.query(func.count(DataException.id))
        .filter(DataException.status == "open")
        .scalar() or 0
    )

    # 健康度评分: 100 - open_exceptions * 2 (floor 0)
    health_score = max(0, min(100, 100 - open_exceptions * 2))

    return {
        "orders": {
            "status_counts": status_counts,
            "trend_30d": order_trend,
            "total_30d": total_orders_30d,
            "revenue_30d": total_revenue_30d,
            "count_7d": orders_7d,
        },
        "inventory": {
            "part_total": part_total,
            "part_negative": part_negative,
            "product_total": prod_total,
            "product_low_stock": prod_low,
        },
        "finance": {
            "alipay_income_30d": _safe_decimal(revenue_30d_alipay),
            "order_revenue_30d": total_revenue_30d,
        },
        "health": {
            "open_exceptions": open_exceptions,
            "health_score": health_score,
        },
    }

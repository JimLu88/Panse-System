"""销售日汇总 (Phase 12).

调度器每天 06:30 跑 rollup(yesterday), 把昨日订单聚合写入 sales_daily_rollup.
查询时优先查 rollup, fallback 到实时算 (近期数据 / 历史水位线之后没 rollup 的).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.sales_rollup import SalesDailyRollup

_logger = logging.getLogger("panse.sales_rollup")


def rollup_day(db: Session, target: date) -> int:
    """聚合某日的所有 (product, sku, platform) 维度. 幂等 (先删再插)."""
    # 删除已有 rollup
    db.execute(
        SalesDailyRollup.__table__.delete().where(SalesDailyRollup.day == target)
    )
    orders = db.execute(
        select(Order).where(
            Order.order_date == target,
            Order.is_historical == False,  # noqa: E712
            Order.status.in_(("paid", "shipped", "signed")),
        )
    ).scalars().all()
    by_key: dict[tuple, dict] = {}
    for o in orders:
        key = (o.product_code or "", o.sku_code or "", o.platform or "")
        d = by_key.setdefault(key, {
            "qty": 0, "order_count": 0, "revenue": Decimal("0"),
            "cost": Decimal("0"), "net_profit": Decimal("0"),
        })
        d["order_count"] += 1
        d["qty"] += o.qty or 0
        paid = Decimal(o.paid_amount or 0)
        cost = Decimal(o.actual_cost or o.theoretical_cost or 0)
        freight = Decimal(o.actual_freight or 0)
        upstairs = Decimal(o.upstairs_fee or 0)
        install = Decimal(o.install_fee or 0)
        comp = Decimal(o.compensation_fee or 0)
        d["revenue"] += paid
        d["cost"] += cost
        d["net_profit"] += paid - cost - freight - upstairs - install - comp

    for (pc, sku, plat), d in by_key.items():
        db.add(SalesDailyRollup(
            day=target, product_code=pc or None,
            sku_code=sku or None, platform=plat or None,
            **d,
        ))
    db.flush()
    return len(by_key)


def rollup_range(db: Session, start: date, end: date) -> dict:
    """补算一段时间. 用于历史回填."""
    d = start
    total = 0
    while d <= end:
        total += rollup_day(db, d)
        d += timedelta(days=1)
    return {"days": (end - start).days + 1, "rows_written": total}


def query_summary(
    db: Session, *, start: date, end: date,
    platform: Optional[str] = None,
) -> dict:
    """从 rollup 查总览. 比直接 SUM orders 表快很多."""
    from sqlalchemy import and_, func
    q = select(
        func.count(SalesDailyRollup.id).label("rollup_rows"),
        func.coalesce(func.sum(SalesDailyRollup.order_count), 0).label("order_count"),
        func.coalesce(func.sum(SalesDailyRollup.qty), 0).label("qty"),
        func.coalesce(func.sum(SalesDailyRollup.revenue), 0).label("revenue"),
        func.coalesce(func.sum(SalesDailyRollup.cost), 0).label("cost"),
        func.coalesce(func.sum(SalesDailyRollup.net_profit), 0).label("net_profit"),
    ).where(
        and_(SalesDailyRollup.day >= start, SalesDailyRollup.day <= end),
    )
    if platform:
        q = q.where(SalesDailyRollup.platform == platform)
    row = db.execute(q).first()
    if row is None or row.rollup_rows == 0:
        return {}
    return {
        "order_count": int(row.order_count or 0),
        "qty": int(row.qty or 0),
        "revenue": float(Decimal(row.revenue or 0)),
        "cost": float(Decimal(row.cost or 0)),
        "net_profit": float(Decimal(row.net_profit or 0)),
        "gross_profit": float(Decimal(row.revenue or 0) - Decimal(row.cost or 0)),
        "source": "rollup",
    }

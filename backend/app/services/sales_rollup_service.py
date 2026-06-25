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
from app.services import order_financials as ofin, sales_analytics as sa

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
            Order.is_refill == False,  # 刷单是假单, 不进销售日汇总 (2026-06-19)
            sa.settled_sale_clause(),   # 统一成交口径(状态6态+实付>0+非全退+非¥0服务行), 与全系统对齐
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
        refund = Decimal(o.refund_amount or 0)
        revenue = paid - refund        # 真实收入=实付−退款, 与 accounting_summary 完全一致(2026-06-20 补D: 原漏减部分退款)
        cost = ofin.physical_cost(o)   # 统一口径: 片段封顶(实付<成本50%→实付×85%)
        # 双算护栏(与 cost_breakdown 对齐, 2026-06-26): physical 已含物流安装的单不另加 —
        # theoretical派生(actual_cost空)、已补非木作(wood_cost_est非空)、定制(走木作占比)都不加;
        # 仅"未补非木作的纯工厂账单单(actual_cost 且 无wood_cost_est 且 非定制)"才加实际运费/安装。
        from app.services import sku_utils
        _cust = bool(getattr(o, "is_custom", False)) or sku_utils.is_custom_sku_code(o.sku_code, o.product_code)
        if o.actual_cost is not None and not Decimal(str(o.wood_cost_est or 0)) and not _cust:
            freight = Decimal(o.actual_freight or 0)
            upstairs = Decimal(o.upstairs_fee or 0)
            install = Decimal(o.install_fee or 0)
        else:
            freight = upstairs = install = Decimal("0")
        comp = Decimal(o.compensation_fee or 0)
        d["revenue"] += revenue
        d["cost"] += cost
        # ⚠ D1(2026-06-25): 此 net_profit 只扣 物理成本+运费+安装+赔付, 【未扣平台扣点/税/额外售后】,
        #   是"毛估利润"非会计净利(比 accounting_summary 偏高 ~2-5%)。此预聚合表目前【无前端消费】
        #   (/sales/rollup-summary 无调用方), 仅作快速毛估。**若要喂月度P&L/经营表, 必须先补齐
        #   platform_deduction + order_tax + 额外售后, 否则会低估成本、虚高利润。**
        d["net_profit"] += revenue - cost - freight - upstairs - install - comp

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

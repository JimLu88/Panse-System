"""工厂对账汇总自动计算 (表 11-工厂对账汇总).

按「对账周期 + 工厂」把工厂下单表 (FactoryOrder) 汇总成一条对账记录:
    本期下单金额 order_amount  = Σ FactoryOrder.expected_amount  (定价表总出厂成本口径)
    工厂账单金额 bill_amount    = Σ FactoryOrder.factory_bill_amount
    实际支付金额 paid_amount    = Σ 匹配到的支付宝支出
    差异金额   diff_amount     = bill_amount - paid_amount
    对账状态   status          = 有支付即 completed, 否则 open
    支付宝流水号 alipay_flow_no = 该周期工厂订单上出现的流水号 (去重)
    对账日期   reconciled_at   = 支付宝流水日期

用法:
    rebuild_for_period(db, factory_name, period_start, period_end) → 单周期重算 (幂等 upsert)
    rebuild_all_periods(db, factory_name=None)                     → 按自然月自动分周期全量重算
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow, FactoryReconciliation
from app.models.order import FactoryOrder

_logger = logging.getLogger("panse.factory_recon")

DEFAULT_FACTORY = "玉山县博冠家具有限公司"


@dataclass
class ReconResult:
    periods: int = 0
    created: int = 0
    updated: int = 0


def _month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    if d.month == 12:
        end = date(d.year, 12, 31)
    else:
        end = date(d.year, d.month + 1, 1) - timedelta(days=1)
    return start, end


def _matched_paid(db: Session, flow_nos: set[str]) -> tuple[Decimal, Optional[date], Optional[str]]:
    """按工厂订单上出现的支付宝流水号, 汇总实际支付金额 + 取最晚付款日 + 流水号串。"""
    if not flow_nos:
        return Decimal("0"), None, None
    flows = db.execute(
        select(AlipayFlow).where(AlipayFlow.transaction_no.in_(flow_nos))
    ).scalars().all()
    total = Decimal("0")
    last_date: Optional[date] = None
    seen: list[str] = []
    for f in flows:
        total += abs(f.amount or Decimal("0"))
        if f.transaction_time:
            d = f.transaction_time.date()
            if last_date is None or d > last_date:
                last_date = d
        if f.transaction_no and f.transaction_no not in seen:
            seen.append(f.transaction_no)
    return total, last_date, ("; ".join(seen) if seen else None)


def rebuild_for_period(
    db: Session, *, factory_name: str, period_start: date, period_end: date,
) -> str:
    """重算单个 (工厂, 周期) 对账记录, 幂等 upsert。返回 'inserted'/'updated'/'skipped'。"""
    fos = db.execute(
        select(FactoryOrder).where(
            FactoryOrder.factory_name == factory_name,
            FactoryOrder.order_date >= period_start,
            FactoryOrder.order_date <= period_end,
            FactoryOrder.voided_at.is_(None),
        )
    ).scalars().all()
    if not fos:
        return "skipped"

    order_amount = sum((fo.expected_amount or Decimal("0")) for fo in fos)
    bill_amount = sum((fo.factory_bill_amount or Decimal("0")) for fo in fos)
    flow_nos = {fo.alipay_flow_no for fo in fos if fo.alipay_flow_no}
    paid_amount, reconciled_at, flow_str = _matched_paid(db, flow_nos)
    diff_amount = (bill_amount or Decimal("0")) - (paid_amount or Decimal("0"))
    status = "completed" if paid_amount > 0 else "open"

    existing = db.execute(
        select(FactoryReconciliation).where(
            FactoryReconciliation.factory_name == factory_name,
            FactoryReconciliation.period_end == period_end,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.period_start = period_start
        existing.order_amount = order_amount
        existing.bill_amount = bill_amount
        existing.paid_amount = paid_amount
        existing.diff_amount = diff_amount
        existing.status = status
        existing.reconciled_at = reconciled_at
        existing.alipay_flow_no = flow_str
        return "updated"

    rec = FactoryReconciliation(
        factory_name=factory_name,
        period_start=period_start,
        period_end=period_end,
        order_amount=order_amount,
        bill_amount=bill_amount,
        paid_amount=paid_amount,
        diff_amount=diff_amount,
        status=status,
        reconciled_at=reconciled_at,
        alipay_flow_no=flow_str,
    )
    db.add(rec)
    return "inserted"


def rebuild_all_periods(db: Session, *, factory_name: Optional[str] = None) -> ReconResult:
    """按自然月自动分周期, 对所有 (工厂, 月份) 重算对账记录。"""
    q = select(FactoryOrder).where(FactoryOrder.voided_at.is_(None))
    if factory_name:
        q = q.where(FactoryOrder.factory_name == factory_name)
    fos = db.execute(q).scalars().all()

    # 按 (工厂, 年月) 分组取月边界
    buckets: dict[tuple[str, tuple[date, date]], bool] = {}
    for fo in fos:
        if fo.order_date is None:
            continue
        fname = fo.factory_name or DEFAULT_FACTORY
        bounds = _month_bounds(fo.order_date)
        buckets[(fname, bounds)] = True

    res = ReconResult()
    for (fname, (start, end)) in buckets:
        action = rebuild_for_period(db, factory_name=fname, period_start=start, period_end=end)
        if action == "inserted":
            res.created += 1
            res.periods += 1
        elif action == "updated":
            res.updated += 1
            res.periods += 1
    db.flush()
    _logger.info("工厂对账重算: %d 个周期 (新建%d 更新%d)", res.periods, res.created, res.updated)
    return res

"""工厂对账汇总自动计算 (表 11-工厂对账汇总).

按「对账周期 + 工厂」把工厂下单表 (FactoryOrder) 汇总成一条对账记录:
    本期下单金额 order_amount  = Σ FactoryOrder.expected_amount  (定价表总出厂成本口径)
    工厂账单金额 bill_amount    = Σ FactoryOrder.factory_bill_amount
    实际支付金额 paid_amount    = Σ 匹配到的支付宝支出
    差异金额   diff_amount     = bill_amount - paid_amount
    对账状态   status          = 按 账单 vs 实付 判定:
        balanced  已对平   |diff| ≤ 容差 且 已有付款
        underpaid 未付清   账单 > 实付 (差额 > 容差)
        overpaid  超付     实付 > 账单 (差额 < -容差)
        unpaid    未付款   实付 = 0
      —— 这样"对账不平/未付清"会被 data_quality 扫描器捞成异常, 在异常中心提示。
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

# 账单 vs 实付 的对平容差 (元): 小于此差额视为已对平 (工厂让利/抹零)。
_BALANCE_TOLERANCE = Decimal("5")


def _recon_status(bill_amount: Decimal, paid_amount: Decimal) -> str:
    """按 账单 vs 实付 判定对账状态。"""
    paid = paid_amount or Decimal("0")
    bill = bill_amount or Decimal("0")
    if paid <= 0:
        return "unpaid"
    diff = bill - paid
    if abs(diff) <= _BALANCE_TOLERANCE:
        return "balanced"
    return "underpaid" if diff > 0 else "overpaid"


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
    status = _recon_status(bill_amount or Decimal("0"), paid_amount or Decimal("0"))

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


_FACTORY_KEYWORDS = ("博冠", "玉山", "家具", "板材", "木")
_BILL_MATCH_TOLERANCE = Decimal("20")   # 账单金额 ±20 元容差


def match_factory_alipay_by_bill_amount(db: Session, *, factory_name: Optional[str] = None) -> int:
    """按对账周期账单金额汇总, 去支付宝支出流水找等额一笔 (±容差), 回填工厂订单 alipay_flow_no。

    命中条件 (同时满足):
      1. 该周期所有工厂订单 factory_bill_amount 之和 ≈ 支付宝支出流水 |amount| (±容差)
      2. 流水对手方含工厂关键字 (博冠/玉山/家具等) 或不限 (宽松模式)
      3. 流水未被其他工厂周期引用

    每个工厂下单的 alipay_flow_no 都更新为匹配到的那笔流水号。
    """
    # 取所有支出流水供匹配
    expense_flows = db.execute(
        select(AlipayFlow).where(AlipayFlow.amount < 0)
    ).scalars().all()

    # 已被引用的流水号 (避免同一笔流水重复分配到多个周期)
    used_flow_nos: set[str] = set()
    for fo in db.execute(
        select(FactoryOrder).where(FactoryOrder.alipay_flow_no.isnot(None))
    ).scalars().all():
        if fo.alipay_flow_no:
            used_flow_nos.add(fo.alipay_flow_no)

    q = select(FactoryOrder).where(
        FactoryOrder.voided_at.is_(None),
        FactoryOrder.factory_bill_amount.isnot(None),
        FactoryOrder.alipay_flow_no.is_(None),   # 只补缺
    )
    if factory_name:
        q = q.where(FactoryOrder.factory_name == factory_name)
    fos = db.execute(q).scalars().all()

    # 按 (工厂, 年月) 分组
    buckets: dict[tuple[str, int, int], list[FactoryOrder]] = defaultdict(list)
    for fo in fos:
        if fo.order_date is None:
            continue
        fname = fo.factory_name or DEFAULT_FACTORY
        buckets[(fname, fo.order_date.year, fo.order_date.month)].append(fo)

    matched_periods = 0
    for (fname, yr, mo), period_fos in buckets.items():
        bill_total = sum(fo.factory_bill_amount or Decimal("0") for fo in period_fos)
        if bill_total <= 0:
            continue

        best_flow: Optional[AlipayFlow] = None
        best_diff = None
        for f in expense_flows:
            if (f.transaction_no or "") in used_flow_nos:
                continue
            flow_abs = abs(f.amount or Decimal("0"))
            diff = abs(flow_abs - bill_total)
            if diff > _BILL_MATCH_TOLERANCE:
                continue
            # 优先对手方含工厂关键词
            cp = (f.counterparty or "").lower()
            is_factory = any(k in cp for k in _FACTORY_KEYWORDS)
            if best_flow is None or diff < best_diff or (diff == best_diff and is_factory):
                best_flow = f
                best_diff = diff

        if best_flow is None:
            continue

        flow_no = best_flow.transaction_no
        for fo in period_fos:
            fo.alipay_flow_no = flow_no
        used_flow_nos.add(flow_no)
        matched_periods += 1

    db.flush()
    _logger.info("工厂流水按账单金额匹配: %d 个周期命中", matched_periods)
    return matched_periods


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

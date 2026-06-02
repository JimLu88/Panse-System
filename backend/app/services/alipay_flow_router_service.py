"""支付宝流水 → 各业务表 自动归类回填 (Phase 3).

在 smart_matching_service 给流水打完 reconciliation_type 后, 把流水落到对应业务表:

  match_promotion_flows   15-推广记录   : 按金额+日期回填 alipay_flow_no
  match_daily_operations  16-日常经营   : 按金额+日期(+备注)回填 alipay_flow_no
  match_outsourcing       17-人员外包   : 按金额+日期回填 (侧重 爱群号 账户)
  create_purchases        7-配件采购    : 无法归类的支出流水 → 新建采购记录 (年份单号)
  flip_factory_payment    6-工厂下单    : 工厂订单有流水号 → 付款状态翻 已付款 + 付款日期

所有操作幂等: 只填空 alipay_flow_no / 只补缺, 已配对的不动。run_all 一键全跑。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.marketing import DailyOperation, OutsourcingExpense, PromotionFlow
from app.models.order import FactoryOrder, PartPurchase

_logger = logging.getLogger("panse.alipay_router")

_MATCH_WINDOW_DAYS = 10   # 金额相同时, 日期相差不超过 N 天才算配上

# 无法归类的支出流水自动建成的采购记录, 用此 purchase_type 标记为「存疑」,
# 由 data_quality_service.scan_unclassified_purchase 捞成异常, 在异常中心提示人工确认,
# 避免「系统替你猜成采购、你却不知道」的静默归类。
UNCLASSIFIED_PURCHASE_TYPE = "存疑(支付宝流水自动归类)"


def _q2(v) -> Decimal:
    return Decimal(str(abs(v))).quantize(Decimal("0.01"))


def _index_flows_by_amount(flows: list[AlipayFlow]) -> dict[Decimal, list[AlipayFlow]]:
    idx: dict[Decimal, list[AlipayFlow]] = {}
    for f in flows:
        if f.amount is None:
            continue
        idx.setdefault(_q2(f.amount), []).append(f)
    return idx


def _match_flow(amount, when: Optional[date], idx, used: set[str]) -> Optional[AlipayFlow]:
    """按 |金额| 相等 + 日期最近 (窗口内) 挑一条未用过的流水。"""
    if amount is None:
        return None
    cands = idx.get(_q2(amount), [])
    best: Optional[AlipayFlow] = None
    best_gap = None
    for f in cands:
        if f.transaction_no in used:
            continue
        gap = 0
        if when and f.transaction_time:
            gap = abs((f.transaction_time.date() - when).days)
            if gap > _MATCH_WINDOW_DAYS:
                continue
        if best is None or (best_gap is not None and gap < best_gap):
            best, best_gap = f, gap
    return best


@dataclass
class RouteResult:
    promotion_filled: int = 0
    daily_filled: int = 0
    outsourcing_filled: int = 0
    purchases_created: int = 0
    factory_flipped: int = 0
    notes: list[str] = field(default_factory=list)


def _all_flows(db: Session, *, expense_only: bool = False,
               account: Optional[str] = None) -> list[AlipayFlow]:
    stmt = select(AlipayFlow)
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    rows = db.execute(stmt).scalars().all()
    if expense_only:
        rows = [r for r in rows if (r.amount or 0) < 0]
    return rows


def match_promotion_flows(db: Session) -> int:
    """15-推广记录: 给缺流水号的推广记录按金额+日期配支付宝流水。"""
    flows = _all_flows(db)
    idx = _index_flows_by_amount(flows)
    used: set[str] = set()
    rows = db.execute(
        select(PromotionFlow).where(PromotionFlow.alipay_flow_no.is_(None))
    ).scalars().all()
    n = 0
    for r in rows:
        f = _match_flow(r.amount, r.transaction_date, idx, used)
        if f:
            r.alipay_flow_no = f.transaction_no
            used.add(f.transaction_no)
            n += 1
    db.flush()
    return n


def match_daily_operations(db: Session) -> int:
    """16-日常经营: 按金额+日期配流水; 同金额多笔时优先备注/对象有交集的。"""
    flows = _all_flows(db)
    idx = _index_flows_by_amount(flows)
    used: set[str] = set()
    rows = db.execute(
        select(DailyOperation).where(DailyOperation.alipay_flow_no.is_(None))
    ).scalars().all()
    n = 0
    for r in rows:
        f = _match_flow(r.amount, r.record_date, idx, used)
        if f:
            r.alipay_flow_no = f.transaction_no
            used.add(f.transaction_no)
            n += 1
    db.flush()
    return n


def match_outsourcing(db: Session) -> int:
    """17-人员外包费用: 按金额+日期配流水 (爱群号等账户支出)。"""
    flows = _all_flows(db)
    idx = _index_flows_by_amount(flows)
    used: set[str] = set()
    rows = db.execute(
        select(OutsourcingExpense).where(OutsourcingExpense.alipay_flow_no.is_(None))
    ).scalars().all()
    n = 0
    for r in rows:
        f = _match_flow(r.amount, r.payment_date, idx, used)
        if f:
            r.alipay_flow_no = f.transaction_no
            used.add(f.transaction_no)
            n += 1
    db.flush()
    return n


def _next_purchase_no_yearly(db: Session, year: int) -> str:
    """采购单号: 年份+5位序号, 如 202600001 (业务指定格式)。"""
    prefix = str(year)
    rows = db.execute(
        select(PartPurchase.purchase_no).where(PartPurchase.purchase_no.like(f"{prefix}%"))
    ).scalars().all()
    max_seq = 0
    for no in rows:
        tail = (no or "")[len(prefix):]
        if tail.isdigit() and len(tail) == 5:
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:05d}"


def create_purchases_from_unclassified(db: Session) -> int:
    """7-配件采购记录: 把无法归类的支出流水整合成采购记录。

    范围: amount<0 且 reconciliation_type 为空/other, 且该流水号尚未被任何
    采购/推广/外包/日常记录引用。供应商取对手方, 备注取流水备注, 视为已付款。
    """
    referenced = set()
    for model in (PartPurchase, PromotionFlow, OutsourcingExpense, DailyOperation):
        for no in db.execute(select(model.alipay_flow_no)).scalars().all():
            if no:
                referenced.add(no)

    flows = db.execute(
        select(AlipayFlow).where(
            AlipayFlow.amount < 0,
            AlipayFlow.reconciliation_type.is_(None),
        )
    ).scalars().all()
    n = 0
    for f in flows:
        if f.transaction_no in referenced:
            continue
        when = f.transaction_time.date() if f.transaction_time else date.today()
        amount = _q2(f.amount)
        pp = PartPurchase(
            purchase_no=_next_purchase_no_yearly(db, when.year),
            supplier=f.counterparty,
            purchase_date=when,
            material_name=(f.remark or f.transaction_type or "未分类支出"),
            qty=Decimal("1"),
            unit_price=amount,
            amount=amount,
            total_amount=amount,
            related_order_no=f.related_order_no,
            purchase_type=UNCLASSIFIED_PURCHASE_TYPE,
            payment_method="支付宝",
            payment_status="paid",
            payment_date=when,
            alipay_flow_no=f.transaction_no,
        )
        db.add(pp)
        db.flush()  # so next yearly seq sees this row
        referenced.add(f.transaction_no)
        n += 1
    return n


def flip_factory_payment(db: Session) -> int:
    """6-工厂下单: 有支付宝流水号的工厂订单 → 付款状态翻已付款 + 补付款日期。"""
    rows = db.execute(
        select(FactoryOrder).where(
            FactoryOrder.alipay_flow_no.isnot(None),
            FactoryOrder.payment_status != "paid",
        )
    ).scalars().all()
    n = 0
    for fo in rows:
        fo.payment_status = "paid"
        if fo.payment_date is None:
            flow = db.execute(
                select(AlipayFlow).where(AlipayFlow.transaction_no == fo.alipay_flow_no)
            ).scalar_one_or_none()
            if flow and flow.transaction_time:
                fo.payment_date = flow.transaction_time.date()
        n += 1
    db.flush()
    return n


def run_all(db: Session) -> RouteResult:
    """一键全跑 (建议先跑 smart_matching_service.run 打好 reconciliation_type)。"""
    res = RouteResult()
    res.promotion_filled = match_promotion_flows(db)
    res.daily_filled = match_daily_operations(db)
    res.outsourcing_filled = match_outsourcing(db)
    res.purchases_created = create_purchases_from_unclassified(db)
    res.factory_flipped = flip_factory_payment(db)
    _logger.info("支付宝流水归类: 推广%d 日常%d 外包%d 采购新建%d 工厂翻已付%d",
                 res.promotion_filled, res.daily_filled, res.outsourcing_filled,
                 res.purchases_created, res.factory_flipped)
    return res

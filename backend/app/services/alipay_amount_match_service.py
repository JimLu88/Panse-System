"""支付宝流水 ↔ 订单 金额匹配 (对账细化 4 规则).

当流水文本里没有订单号 (alipay_backfill 匹配不到) 时, 用 金额 + 日期 + 账户语义 补匹配。
全程保守: 只认「唯一」候选, 只填空 (only_missing), 不覆盖已匹配 —— 是真金白银, 宁缺勿错。

规则 (优先级从稳到松):
  R2 金额唯一锁定: 某金额在「未匹配订单」与「未匹配收入流水」里各只出现一次 → 直接锁定。
  R1 金额+日期模糊: 订单金额≈流水金额 (容差) 且 日期相近 (窗口内), 唯一候选 → 匹配。
  R3 多对一/一对多: 一笔流水==多订单之和, 或 一订单==多流水之和 (2 笔、同窗口、唯一组合) → 关联。
  R4 账户语义: 只用收入流水 (amount>0) 配订单 (客户回款); 爱群/佳宝为历史弃用账户, 默认排除。

只读预览 analyze(); 落库 match()。
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.order import Order

_CENT = Decimal("0.01")
# 账户语义 (R4): 这两个账号未来弃用, 默认不参与新匹配 (历史数据已另行处理)。
DEPRECATED_ACCOUNTS = ("爱群", "佳宝")


@dataclass
class AmountMatch:
    order_no: str
    flow_nos: list[str]
    rule: str            # amount_unique / amount_date / split_order_to_flows / merge_flows_to_order
    amount: Decimal


@dataclass
class AmountMatchResult:
    candidate_orders: int = 0
    candidate_flows: int = 0
    matched: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)
    linked_flow_no: int = 0
    samples: list[dict] = field(default_factory=list)


def _flow_date(f: AlipayFlow) -> Optional[date]:
    return f.transaction_time.date() if f.transaction_time else None


def _is_deprecated(account: Optional[str]) -> bool:
    a = account or ""
    return any(k in a for k in DEPRECATED_ACCOUNTS)


def _load_candidates(db: Session, *, include_deprecated: bool):
    """未匹配订单 (有金额、无流水号、未取消) + 未匹配收入流水 (amount>0、未对账)。"""
    orders = [
        o for o in db.execute(select(Order)).scalars().all()
        if o.alipay_flow_no is None
        and o.paid_amount is not None and o.paid_amount > 0
        and o.status != "cancelled"
    ]
    flows = [
        f for f in db.execute(select(AlipayFlow)).scalars().all()
        if f.amount is not None and f.amount > 0                  # R4: 收入流水才配订单(客户回款)
        and f.reconciliation_status != "matched"
        and (include_deprecated or not _is_deprecated(f.account))  # R4: 默认排除爱群/佳宝
    ]
    return orders, flows


def _compute(db: Session, *, days_window: int, amount_tol: Decimal,
             include_deprecated: bool) -> tuple[list[AmountMatch], AmountMatchResult]:
    orders, flows = _load_candidates(db, include_deprecated=include_deprecated)
    res = AmountMatchResult(candidate_orders=len(orders), candidate_flows=len(flows))
    used_flow: set[int] = set()
    used_order: set[str] = set()
    matches: list[AmountMatch] = []

    def q(x) -> Decimal:
        return Decimal(x).quantize(_CENT)

    # ---- R2 金额唯一锁定 ----
    ord_by_amt: dict[Decimal, list[Order]] = defaultdict(list)
    flow_by_amt: dict[Decimal, list[AlipayFlow]] = defaultdict(list)
    for o in orders:
        ord_by_amt[q(o.paid_amount)].append(o)
    for f in flows:
        flow_by_amt[q(f.amount)].append(f)
    for amt, os in ord_by_amt.items():
        fs = flow_by_amt.get(amt)
        if len(os) == 1 and fs and len(fs) == 1:
            o, f = os[0], fs[0]
            matches.append(AmountMatch(o.order_no, [f.transaction_no], "amount_unique", amt))
            used_order.add(o.order_no); used_flow.add(id(f))

    # ---- R1 金额+日期 (唯一候选) ----
    rem_flows = [f for f in flows if id(f) not in used_flow]
    for o in orders:
        if o.order_no in used_order or o.order_date is None:
            continue
        cands = [
            f for f in rem_flows
            if id(f) not in used_flow
            and abs(q(f.amount) - q(o.paid_amount)) <= amount_tol
            and _flow_date(f) is not None
            and abs((_flow_date(f) - o.order_date).days) <= days_window
        ]
        if len(cands) == 1:
            f = cands[0]
            matches.append(AmountMatch(o.order_no, [f.transaction_no], "amount_date", q(o.paid_amount)))
            used_order.add(o.order_no); used_flow.add(id(f))

    # ---- R3 多对一/一对多 (2 笔、唯一组合) ----
    # 一对多: 一个订单 == 两笔流水之和 (同窗口)
    rem_flows = [f for f in flows if id(f) not in used_flow]
    for o in orders:
        if o.order_no in used_order or o.order_date is None:
            continue
        near = [f for f in rem_flows if id(f) not in used_flow and _flow_date(f) is not None
                and abs((_flow_date(f) - o.order_date).days) <= days_window]
        combos = [(a, b) for a, b in itertools.combinations(near, 2)
                  if abs(q(a.amount) + q(b.amount) - q(o.paid_amount)) <= amount_tol]
        if len(combos) == 1:
            a, b = combos[0]
            matches.append(AmountMatch(o.order_no, [a.transaction_no, b.transaction_no],
                                       "split_order_to_flows", q(o.paid_amount)))
            used_order.add(o.order_no); used_flow.add(id(a)); used_flow.add(id(b))

    # 多对一: 一笔流水 == 两个订单之和 (同窗口)
    rem_orders = [o for o in orders if o.order_no not in used_order and o.order_date is not None]
    for f in flows:
        if id(f) in used_flow or _flow_date(f) is None:
            continue
        near = [o for o in rem_orders if o.order_no not in used_order
                and abs((_flow_date(f) - o.order_date).days) <= days_window]
        combos = [(a, b) for a, b in itertools.combinations(near, 2)
                  if abs(q(a.paid_amount) + q(b.paid_amount) - q(f.amount)) <= amount_tol]
        if len(combos) == 1:
            a, b = combos[0]
            for o in (a, b):
                matches.append(AmountMatch(o.order_no, [f.transaction_no], "merge_flows_to_order", q(o.paid_amount)))
                used_order.add(o.order_no)
            used_flow.add(id(f))

    res.matched = len({m.order_no for m in matches})
    for m in matches:
        res.by_rule[m.rule] = res.by_rule.get(m.rule, 0) + 1
    return matches, res


def analyze(db: Session, *, days_window: int = 3, amount_tol: Decimal = _CENT,
            include_deprecated: bool = False, sample_limit: int = 20) -> AmountMatchResult:
    matches, res = _compute(db, days_window=days_window, amount_tol=amount_tol,
                            include_deprecated=include_deprecated)
    for m in matches[:sample_limit]:
        res.samples.append({"order_no": m.order_no, "flow_nos": m.flow_nos,
                            "rule": m.rule, "amount": str(m.amount)})
    return res


def match(db: Session, *, days_window: int = 3, amount_tol: Decimal = _CENT,
          include_deprecated: bool = False) -> AmountMatchResult:
    """落库: 把唯一命中的流水号写回 Order.alipay_flow_no, 并标流水 reconciliation_status='matched'。"""
    matches, res = _compute(db, days_window=days_window, amount_tol=amount_tol,
                            include_deprecated=include_deprecated)
    linked = 0
    for m in matches:
        o = db.execute(select(Order).where(Order.order_no == m.order_no)).scalar_one_or_none()
        if o is None or o.alipay_flow_no:   # only_missing
            continue
        o.alipay_flow_no = m.flow_nos[0]    # 多笔时记主流水, 其余在对账明细可见
        linked += 1
        for fno in m.flow_nos:
            f = db.execute(
                select(AlipayFlow).where(AlipayFlow.transaction_no == fno).limit(1)
            ).scalar_one_or_none()
            if f is not None and f.reconciliation_status != "matched":
                f.reconciliation_status = "matched"
    db.flush()
    res.linked_flow_no = linked
    return res

"""账户余额服务 (plan §9 期初 + 本月收支 → 期末)。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, extract, func, or_, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, AlipayFlow


def recompute_month(
    db: Session,
    *,
    account: str,
    year: int,
    month: int,
    opening_balance: Optional[Decimal] = None,
) -> AccountBalance:
    """重算指定账户某月的收入/支出/期末。

    opening_balance:
        - 显式传入 → 用这个（适合期初调整）
        - 否则取上月的 closing_balance；若上月没记录 → 0
    """
    if not (1 <= month <= 12):
        raise ValueError("month must be 1..12")

    # 算本月 income / expense
    income_q = select(func.coalesce(func.sum(AlipayFlow.amount), 0)).where(
        and_(
            AlipayFlow.account == account,
            AlipayFlow.amount > 0,
            extract("year", AlipayFlow.transaction_time) == year,
            extract("month", AlipayFlow.transaction_time) == month,
            or_(
                AlipayFlow.reconciliation_type.is_(None),
                AlipayFlow.reconciliation_type != "opening",
            ),
        )
    )
    expense_q = select(func.coalesce(func.sum(-AlipayFlow.amount), 0)).where(
        and_(
            AlipayFlow.account == account,
            AlipayFlow.amount < 0,
            extract("year", AlipayFlow.transaction_time) == year,
            extract("month", AlipayFlow.transaction_time) == month,
            or_(
                AlipayFlow.reconciliation_type.is_(None),
                AlipayFlow.reconciliation_type != "opening",
            ),
        )
    )
    income = Decimal(db.execute(income_q).scalar() or 0)
    expense = Decimal(db.execute(expense_q).scalar() or 0)

    if opening_balance is None:
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        prev = db.execute(
            select(AccountBalance).where(
                AccountBalance.account_name == account,
                AccountBalance.period_year == prev_year,
                AccountBalance.period_month == prev_month,
            )
        ).scalar_one_or_none()
        opening_balance = prev.closing_balance if prev else Decimal("0")

    closing = (opening_balance + income - expense).quantize(Decimal("0.01"))

    row = db.execute(
        select(AccountBalance).where(
            AccountBalance.account_name == account,
            AccountBalance.period_year == year,
            AccountBalance.period_month == month,
        )
    ).scalar_one_or_none()
    if row is None:
        row = AccountBalance(
            account_name=account,
            period_year=year,
            period_month=month,
        )
        db.add(row)
    row.opening_balance = opening_balance
    row.income = income
    row.expense = expense
    row.closing_balance = closing
    db.flush()
    return row


def derive_opening_balance(db: Session, *, account: str, target_date: date) -> dict:
    """Plan F10: 期初余额倒推工具。

    取 as_of_date >= target_date 的最近余额快照, 减去 (target_date, as_of_date] 区间的
    Σ AlipayFlow.amount (带符号) → 推出 target_date 当日的期初余额。
    顺带报告区间内"无流水天数" gaps (可能是漏导入, 提醒核对)。
    """
    snap = db.execute(
        select(AccountBalance).where(
            AccountBalance.account_name == account,
            AccountBalance.as_of_date.isnot(None),
            AccountBalance.as_of_date >= target_date,
        ).order_by(AccountBalance.as_of_date.asc()).limit(1)
    ).scalar_one_or_none()
    if snap is None:
        return {"ok": False,
                "message": f"{account} 在 {target_date} 之后没有带统计日期的余额快照, 无法倒推"}
    rows = db.execute(
        select(AlipayFlow.transaction_time, AlipayFlow.amount).where(
            AlipayFlow.account == account,
            AlipayFlow.transaction_time.isnot(None),
        )
    ).all()
    net = Decimal("0")
    flow_days: set[date] = set()
    for t, amt in rows:
        d = t.date()
        if target_date < d <= snap.as_of_date:
            net += Decimal(amt or 0)
            flow_days.add(d)
    span_days = (snap.as_of_date - target_date).days
    gaps = max(0, span_days - len(flow_days))
    derived = (Decimal(snap.closing_balance or 0) - net).quantize(Decimal("0.01"))
    return {
        "ok": True,
        "account": account,
        "target_date": target_date.isoformat(),
        "snapshot_date": snap.as_of_date.isoformat(),
        "snapshot_balance": float(snap.closing_balance or 0),
        "interval_net_flow": float(net),
        "derived_balance": float(derived),
        "span_days": span_days,
        "days_with_flows": len(flow_days),
        "gap_days": gaps,
        "hint": (f"区间 {span_days} 天里有 {gaps} 天没有任何流水"
                 + (", 若该账户日常有交易, 可能漏导入, 结果仅供参考" if gaps > max(1, span_days) // 2 else "")),
    }


def insert_opening_adjustment(
    db: Session,
    *,
    account: str,
    amount: Decimal,
    when: Optional[date] = None,
    note: str = "Phase 4 期初余额一次性调整 (plan §9)",
) -> AlipayFlow:
    """插入一条期初调整流水：amount > 0 表示给账户加这么多期初。"""
    from datetime import datetime
    txn_time = datetime.combine(when or date(2025, 12, 31), datetime.min.time())
    flow = AlipayFlow(
        account=account,
        transaction_no=f"OPENING-{account}-{txn_time.date().isoformat()}",
        transaction_time=txn_time,
        transaction_type="opening_balance",
        amount=amount,
        balance=amount,
        reconciliation_status="opening_balance",
        reconciliation_type="opening",
        remark=note,
    )
    db.add(flow)
    db.flush()
    return flow

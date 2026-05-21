"""账户余额服务 (plan §9 期初 + 本月收支 → 期末)。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, extract, func, select
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
            AlipayFlow.reconciliation_type != "opening",
        )
    )
    expense_q = select(func.coalesce(func.sum(-AlipayFlow.amount), 0)).where(
        and_(
            AlipayFlow.account == account,
            AlipayFlow.amount < 0,
            extract("year", AlipayFlow.transaction_time) == year,
            extract("month", AlipayFlow.transaction_time) == month,
            AlipayFlow.reconciliation_type != "opening",
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

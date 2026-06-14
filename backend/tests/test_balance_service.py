from datetime import datetime
from decimal import Decimal

import pytest

from app.models.finance import AccountBalance, AlipayFlow
from app.services import balance_service


def _flow(db, account, ts, amount, tx_no, type_="other"):
    db.add(AlipayFlow(
        account=account, transaction_no=tx_no, transaction_time=ts,
        amount=Decimal(str(amount)),
        reconciliation_type=type_,
    ))
    db.flush()


def test_recompute_zero_state(db_session):
    row = balance_service.recompute_month(db_session, account="企业号", year=2026, month=5)
    assert row.opening_balance == Decimal("0")
    assert row.income == Decimal("0")
    assert row.expense == Decimal("0")
    assert row.closing_balance == Decimal("0")


def test_recompute_sums_income_and_expense(db_session):
    _flow(db_session, "企业号", datetime(2026, 5, 10), 1000, "T1")
    _flow(db_session, "企业号", datetime(2026, 5, 15), -300, "T2")
    _flow(db_session, "企业号", datetime(2026, 5, 20), 200, "T3")
    # 不同账户的不算
    _flow(db_session, "私账", datetime(2026, 5, 10), 999, "T4")
    # 不同月份不算
    _flow(db_session, "企业号", datetime(2026, 6, 1), 500, "T5")
    row = balance_service.recompute_month(db_session, account="企业号", year=2026, month=5)
    assert row.income == Decimal("1200")
    assert row.expense == Decimal("300")
    assert row.closing_balance == Decimal("900.00")


def test_recompute_uses_prev_month_closing(db_session):
    # 4 月期末 = 500，5 月新增收入 100 → 期末 600
    db_session.add(AccountBalance(
        account_name="企业号", period_year=2026, period_month=4,
        opening_balance=Decimal("0"), income=Decimal("500"), expense=Decimal("0"),
        closing_balance=Decimal("500"),
    ))
    _flow(db_session, "企业号", datetime(2026, 5, 1), 100, "T1")
    row = balance_service.recompute_month(db_session, account="企业号", year=2026, month=5)
    assert row.opening_balance == Decimal("500")
    assert row.closing_balance == Decimal("600.00")


def test_recompute_explicit_opening_overrides(db_session):
    _flow(db_session, "企业号", datetime(2026, 5, 1), 100, "T1")
    row = balance_service.recompute_month(
        db_session, account="企业号", year=2026, month=5, opening_balance=Decimal("9999"),
    )
    assert row.opening_balance == Decimal("9999")
    assert row.closing_balance == Decimal("10099.00")


def test_opening_adjustment_excluded_from_month(db_session):
    # 期初调整不计入当月 income
    balance_service.insert_opening_adjustment(
        db_session, account="企业号", amount=Decimal("5000")
    )
    db_session.commit()
    # 2025-12-31 的调整不会出现在 2026-05 的统计
    row = balance_service.recompute_month(db_session, account="企业号", year=2026, month=5)
    assert row.income == Decimal("0")


def test_invalid_month_rejected(db_session):
    with pytest.raises(ValueError):
        balance_service.recompute_month(db_session, account="x", year=2026, month=13)


def test_recompute_is_idempotent(db_session):
    _flow(db_session, "企业号", datetime(2026, 5, 1), 100, "T1")
    r1 = balance_service.recompute_month(db_session, account="企业号", year=2026, month=5)
    r2 = balance_service.recompute_month(db_session, account="企业号", year=2026, month=5)
    assert r1.id == r2.id
    assert r2.closing_balance == Decimal("100.00")
    assert db_session.query(AccountBalance).count() == 1


def test_recompute_includes_null_reconciliation_type(db_session):
    # 回归 (2026-06-14): reconciliation_type=NULL 的流水(标准支付宝导入即如此)必须计入当月收支。
    # 旧 bug: `!= "opening"` 在 SQL 里对 NULL 求值为 NULL → 整条被 WHERE 排除, 期末余额系统性少算。
    _flow(db_session, "企业号", datetime(2026, 5, 10), 1000, "N1", type_=None)
    _flow(db_session, "企业号", datetime(2026, 5, 12), -400, "N2", type_=None)
    row = balance_service.recompute_month(db_session, account="企业号", year=2026, month=5)
    assert row.income == Decimal("1000")
    assert row.expense == Decimal("400")
    assert row.closing_balance == Decimal("600.00")

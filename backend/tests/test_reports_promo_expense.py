"""回归锁: 月度报表「推广费」只能算 PromotionFlow.flow_type == "支出"。

背景 (2026-06-10): 用户发现月度报表推广费偏高约一倍 —— `_business_month` 当时
把「充值/收入」与「支出」一起 sum 了。修复后只取「支出」。本文件钉死该行为:
同一月同时存在 充值 与 支出 时, promo_expense 必须只等于支出之和。
若将来有人去掉 flow_type=="支出" 过滤, 这些测试会立刻失败。
"""
from datetime import date
from decimal import Decimal

from app.api.reports import _business_month
from app.models.marketing import PromotionFlow


def test_promo_expense_counts_spend_only_not_recharge(db_session):
    # 5月: 支出 3000 + 500 = 3500; 充值 9999 绝不计入推广费
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 3), flow_type="支出", amount=Decimal("3000")))
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 9), flow_type="充值", amount=Decimal("9999")))
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 20), flow_type="支出", amount=Decimal("500")))
    db_session.flush()

    row = _business_month(db_session, 2026, 5)
    assert row["promo_expense"] == 3500.0  # 不是 3500+9999=13499


def test_promo_expense_zero_when_only_recharge(db_session):
    # 整月只有充值, 没有支出 → 推广费应为 0 (不能把充值当推广费)
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 1), flow_type="充值", amount=Decimal("8000")))
    db_session.flush()

    assert _business_month(db_session, 2026, 5)["promo_expense"] == 0.0


def test_promo_expense_scoped_to_month(db_session):
    # 跨月: 只统计当月支出
    db_session.add(PromotionFlow(transaction_date=date(2026, 4, 30), flow_type="支出", amount=Decimal("111")))
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 1), flow_type="支出", amount=Decimal("222")))
    db_session.add(PromotionFlow(transaction_date=date(2026, 6, 1), flow_type="支出", amount=Decimal("333")))
    db_session.flush()

    assert _business_month(db_session, 2026, 5)["promo_expense"] == 222.0

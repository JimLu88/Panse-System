"""经营支出自动配流水 (expense_flow_match_service) 测试。"""
from datetime import date, datetime
from decimal import Decimal

from app.models.field_change import FieldChange
from app.models.finance import AlipayFlow
from app.models.marketing import BrandMarketing, DailyOperation
from app.services.expense_flow_match_service import match_expense_flows


def test_unique_match_fills_and_traces(db_session):
    # Arrange: 一笔缺流水号的日常支出 + 唯一等额支出流水
    db_session.add(DailyOperation(
        record_date=date(2026, 3, 5), item="打印机", amount=Decimal("388"),
    ))
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="EXP-1",
        transaction_time=datetime(2026, 3, 6), amount=Decimal("-388"),
    ))
    db_session.flush()

    # Act
    res = match_expense_flows(db_session, actor="测试员")

    # Assert: 回填 + 修改档案留痕
    assert res.matched["日常经营"] == 1
    row = db_session.query(DailyOperation).one()
    assert row.alipay_flow_no == "EXP-1"
    fc = db_session.query(FieldChange).filter(
        FieldChange.table_name == "daily_operations",
        FieldChange.field == "alipay_flow_no",
    ).one()
    assert fc.new_value == "EXP-1"


def test_ambiguous_not_filled(db_session):
    # Arrange: 同金额两笔流水都在窗口内 → 不猜, 留人工
    db_session.add(DailyOperation(
        record_date=date(2026, 3, 5), item="耗材", amount=Decimal("200"),
    ))
    for i, day in enumerate((4, 7)):
        db_session.add(AlipayFlow(
            account="企业号", transaction_no=f"EXP-A{i}",
            transaction_time=datetime(2026, 3, day), amount=Decimal("-200"),
        ))
    db_session.flush()

    res = match_expense_flows(db_session)

    assert res.ambiguous == 1
    assert db_session.query(DailyOperation).one().alipay_flow_no is None


def test_brand_marketing_covered_and_window_respected(db_session):
    # Arrange: 品牌营销在窗口内有唯一流水; 窗口外 (>10天) 的另一笔不算候选
    db_session.add(BrandMarketing(
        project_name="小红书投放", actual_spend=Decimal("5000"),
        payment_date=date(2026, 4, 10),
    ))
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="EXP-B1",
        transaction_time=datetime(2026, 4, 12), amount=Decimal("-5000"),
    ))
    db_session.add(AlipayFlow(   # 同金额但隔 40 天 → 窗口外
        account="企业号", transaction_no="EXP-B2",
        transaction_time=datetime(2026, 5, 20), amount=Decimal("-5000"),
    ))
    db_session.flush()

    res = match_expense_flows(db_session)

    assert res.matched["品牌营销"] == 1
    assert db_session.query(BrandMarketing).one().alipay_flow_no == "EXP-B1"

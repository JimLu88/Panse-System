# -*- coding: utf-8 -*-
"""营收对账纳入 order_settlements 交易收款 (聚合/微信收款, 用户 2026-06-29)。

聚合结算账户收款走 billDetail → order_settlements 的『交易收款』, 不进 alipay_flows。
营收对账过去只看 alipay_flows → 聚合付款订单假报"未配到流水"。本测试验证: 该单被认领、不再误报。
"""
from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.models.settlement import OrderSettlement
from app.services import reconciliation_service as rs


def test_revenue_alipay_counts_aggregate_settlement(db_session):
    """聚合付款订单(收款仅在 order_settlements 交易收款)→ 营收对账认它为该单收入, 不算"未配到流水"。"""
    db_session.add(Order(platform="淘宝", order_no="AGG1", status="signed", is_refill=False,
                         order_date=date(2026, 3, 1), paid_amount=Decimal("3183.78"),
                         buyer_payable_amount=Decimal("3183.78")))
    db_session.add(OrderSettlement(source="agent", pay_no="PAYAGG1", order_no="AGG1",
                                   entry_type="交易收款", income=Decimal("3183.78"), expense=Decimal("0")))
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    # 2026-03 月度兜底的"未配到流水的订单实付"应为 0(AGG1 被聚合结算认领); 旧口径会是 3183.78
    march = [d for d in res.diffs if d.key == "2026-03 兜底"]
    assert not march or (march[0].expected or 0) == 0


def test_settlement_deduction_not_counted_as_income(db_session):
    """『扣款』(软件服务费, income=0) 不算收款 → 不影响该单收入认领。"""
    db_session.add(Order(platform="淘宝", order_no="AGG2", status="signed", is_refill=False,
                         order_date=date(2026, 3, 1), paid_amount=Decimal("100"),
                         buyer_payable_amount=Decimal("100")))
    db_session.add(OrderSettlement(source="agent", pay_no="PAYAGG2", order_no="AGG2",
                                   entry_type="交易收款", income=Decimal("100"), expense=Decimal("0")))
    db_session.add(OrderSettlement(source="agent", pay_no="FEEAGG2", order_no="AGG2",
                                   entry_type="扣款", income=Decimal("0"), expense=Decimal("0.13")))
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    march = [d for d in res.diffs if d.key == "2026-03 兜底"]
    assert not march or (march[0].expected or 0) == 0

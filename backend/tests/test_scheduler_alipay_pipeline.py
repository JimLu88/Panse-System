"""每日自动逐笔对账 pipeline: 已注册 + 能把唯一金额订单↔流水对上。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.models.order import Order
from app.services import scheduler


def test_pipeline_job_registered():
    scheduler._register_default_jobs()
    ids = {j["job_id"] for j in scheduler.list_jobs()}
    assert "daily_0940_alipay_match" in ids


def test_pipeline_links_order_by_amount(db_session):
    db_session.add(Order(platform="淘宝", order_no="O1", paid_amount=Decimal("888"),
                         order_date=date(2026, 6, 1), status="signed"))
    db_session.add(AlipayFlow(account="主力号", transaction_no="F1", amount=Decimal("888"),
                              transaction_time=datetime(2026, 6, 1, 10, 0), reconciliation_status="open"))
    db_session.commit()
    res = scheduler._job_alipay_match_pipeline(db_session)
    db_session.commit()
    assert db_session.query(Order).filter_by(order_no="O1").one().alipay_flow_no == "F1"
    assert res["amount_match"]["matched"] >= 1

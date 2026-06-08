# -*- coding: utf-8 -*-
"""工厂订单付款状态对账回填测试 — 消除工厂欠款虚高。"""
from datetime import date, timedelta
from decimal import Decimal

from app.models.order import FactoryOrder, Order
from app.services import factory_payment_service as F


def _fo(db, no, *, platform_no=None, source_id=None, order_date=None,
        flow_no=None, pay_date=None, status="unpaid"):
    db.add(FactoryOrder(factory_order_no=no, platform_order_no=platform_no,
                        source_order_id=source_id, order_date=order_date,
                        alipay_flow_no=flow_no, payment_date=pay_date,
                        payment_status=status, factory_bill_amount=Decimal("1000")))


def _order(db, no, status, order_date):
    db.add(Order(platform="淘宝", order_no=no, status=status, order_date=order_date))


def test_backfill_evidence_and_settled(db_session):
    db = db_session
    today = date.today()
    old = today - timedelta(days=60)
    recent = today - timedelta(days=5)

    _fo(db, "E1", flow_no="T123")                                  # 证据: 有流水号
    _fo(db, "E2", pay_date=today)                                  # 证据: 有付款日
    _order(db, "O_OLD", "signed", old)
    _fo(db, "S1", platform_no="O_OLD", order_date=old)            # 推断: 关联已签收+超周期
    _order(db, "O_NEW", "signed", recent)
    _fo(db, "S2", platform_no="O_NEW", order_date=recent)        # 近期已签收 → 仍未付
    _order(db, "O_ACT", "paid", old)
    _fo(db, "U1", platform_no="O_ACT", order_date=old)           # 关联未签收 → 仍未付
    _fo(db, "U2", order_date=old)                                 # 无关联 → 仍未付
    db.flush()

    r = F.backfill_payment_status(db)
    assert r["by_evidence"] == 2
    assert r["by_settled"] == 1
    assert r["still_unpaid"] == 3

    paid = {fo.factory_order_no for fo in db.query(FactoryOrder).filter_by(payment_status="paid")}
    assert paid == {"E1", "E2", "S1"}
    # 推断回填的写了备注便于审计
    s1 = db.query(FactoryOrder).filter_by(factory_order_no="S1").one()
    assert "对账回填" in (s1.remark or "") and s1.payment_date is not None


def test_inference_can_be_disabled(db_session):
    db = db_session
    old = date.today() - timedelta(days=90)
    _order(db, "O1", "signed", old)
    _fo(db, "S1", platform_no="O1", order_date=old)
    db.flush()
    r = F.backfill_payment_status(db, apply_settled_inference=False)
    assert r["by_settled"] == 0 and r["still_unpaid"] == 1
    assert db.query(FactoryOrder).filter_by(factory_order_no="S1").one().payment_status == "unpaid"


def test_dry_run_no_change(db_session):
    db = db_session
    _fo(db, "E1", flow_no="T1")
    db.flush()
    r = F.backfill_payment_status(db, dry_run=True)
    assert r["by_evidence"] == 1 and r["dry_run"] is True
    assert db.query(FactoryOrder).filter_by(factory_order_no="E1").one().payment_status == "unpaid"

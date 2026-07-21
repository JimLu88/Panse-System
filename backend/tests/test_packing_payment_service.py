# -*- coding: utf-8 -*-
from datetime import datetime
from decimal import Decimal

import pytest

from app.models.finance import AlipayFlow, PackingBill, PackingPaymentAllocation
from app.services import packing_payment_service as svc
from app.services import reconciliation_service as recon


def _flow(db, no, amount, remark, *, when=None, recon_type=None):
    row = AlipayFlow(
        account="主力号", transaction_no=no, transaction_time=when,
        transaction_type="转账", amount=Decimal(str(amount)), remark=remark,
        reconciliation_status="open", reconciliation_type=recon_type,
    )
    db.add(row)
    db.flush()
    return row


def _bill(db, month, amount):
    db.add(PackingBill(bill_month=month, customer_name="测试", packing_fee=Decimal(str(amount))))
    db.flush()


def test_suggested_month_uses_service_month_not_payment_month(db_session):
    flow = _flow(db_session, "P1", -7195, "4月打包费",
                 when=datetime(2026, 5, 14, 10, 0))
    assert svc.suggested_months(flow) == ["2026-04"]


def test_suggested_month_uses_unique_known_year_when_legacy_flow_has_no_date(db_session):
    flow = _flow(db_session, "P0", -100, "挚乐2月打包费")
    assert svc.suggested_months(flow, {"2026-01", "2026-02"}) == ["2026-02"]


def test_auto_allocate_strong_single_month_and_reclassify(db_session):
    _bill(db_session, "2026-05", 11215)
    flow = _flow(db_session, "P2", -11215, "5月打包费用",
                 when=datetime(2026, 6, 20, 10, 0), recon_type="factory_payment")
    result = svc.auto_allocate(db_session)
    allocation = db_session.query(PackingPaymentAllocation).one()
    assert result["allocated"] == 1
    assert allocation.bill_month == "2026-05"
    assert allocation.amount == Decimal("11215.00")
    assert flow.reconciliation_type == "packing_payment"


def test_auto_allocate_leaves_cross_month_for_manual_review(db_session):
    _bill(db_session, "2026-01", 1000)
    _bill(db_session, "2026-02", 2000)
    _flow(db_session, "P3", -3000, "1月2月打包费",
          when=datetime(2026, 3, 1, 10, 0))
    result = svc.auto_allocate(db_session)
    assert result["allocated"] == 0
    assert result["needs_review"] == 1
    assert db_session.query(PackingPaymentAllocation).count() == 0


def test_manual_allocation_supports_split_and_caps_total(db_session):
    flow = _flow(db_session, "P4", -3000, "1月2月打包费")
    svc.create_allocation(db_session, flow_id=flow.id, bill_month="2026-01", amount=1000)
    svc.create_allocation(db_session, flow_id=flow.id, bill_month="2026-02", amount=2000)
    assert db_session.query(PackingPaymentAllocation).count() == 2
    with pytest.raises(ValueError, match="累计分配不能超过"):
        svc.create_allocation(db_session, flow_id=flow.id, bill_month="2026-03", amount=1)


def test_month_reconciliation_balanced(db_session):
    _bill(db_session, "2020-01", 500)
    flow = _flow(db_session, "P5", -500, "2020年1月打包费",
                 when=datetime(2020, 2, 1, 10, 0))
    svc.create_allocation(db_session, flow_id=flow.id, bill_month="2020-01", amount=500)
    result = recon.run_packing_payment(db_session, record_exceptions=True)
    row = next(d for d in result.diffs if d.key == "2020-01")
    assert row.expected == Decimal("500.0")
    assert row.actual == Decimal("500.0")
    assert row.severity == "ok"


def test_month_reconciliation_overdue_unpaid_is_error(db_session):
    _bill(db_session, "2020-02", 500)
    result = recon.run_packing_payment(db_session, record_exceptions=True)
    row = next(d for d in result.diffs if d.key == "2020-02")
    assert row.actual == Decimal("0.0")
    assert row.severity == "error"

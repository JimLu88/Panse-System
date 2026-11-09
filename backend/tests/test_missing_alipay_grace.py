# -*- coding: utf-8 -*-
"""缺收款凭据按淘宝客户确认收货日期延后核查，物流签收不算放款。"""
from datetime import date, timedelta
from decimal import Decimal as D

from app.models.exception import DataException
from app.models.field_change import FieldChange
from app.models.order import Order
from app.services import data_quality_service as dq
from app.services import exception_recheck_service as er


def _order(db, ono, **kw):
    kw.setdefault("platform", "淘宝")
    kw.setdefault("status", "signed")
    kw.setdefault("is_historical", False)
    kw.setdefault("order_date", date.today() - timedelta(days=60))
    kw.setdefault("product_name", "畔色实木餐桌")
    kw.setdefault("paid_amount", D("1000"))
    o = Order(order_no=ono, **kw)
    db.add(o)
    return o


def _excs(db, ono):
    return [e for e in db.query(DataException).filter(
        DataException.exception_type == "order_missing_alipay",
        DataException.status == "open").all() if ono in str(e.description)]


def test_old_signed_without_evidence_still_flagged(db_session):
    """客户确认收货已超过三天而无凭据 → 报警。"""
    _order(db_session, "G-OLD1", ship_date=date.today() - timedelta(days=40),
           customer_confirmed_date=date.today() - timedelta(days=4))
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert _excs(db_session, "G-OLD1")


def test_recently_signed_not_flagged(db_session):
    """物流近期签收但客户未确认收货 → 不报。"""
    _order(db_session, "G-NEW1", ship_date=date.today() - timedelta(days=40))
    db_session.add(FieldChange(table_name="orders", row_pk="G-NEW1", field="status",
                               old_value="paid", new_value="signed", actor="订单重导", source="import"))
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert not _excs(db_session, "G-NEW1")


def test_recent_shipdate_not_flagged(db_session):
    """未确认收货不因近期发货或 signed 状态报警。"""
    _order(db_session, "G-SHIP1", ship_date=date.today() - timedelta(days=5))
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert not _excs(db_session, "G-SHIP1")


def test_checker_closes_recently_signed(db_session):
    """复核器同口径: 客户未确认收货，已有异常应销账。"""
    o = _order(db_session, "G-NEW2", ship_date=date.today() - timedelta(days=40))
    db_session.flush()
    db_session.add(FieldChange(table_name="orders", row_pk="G-NEW2", field="status",
                               old_value="paid", new_value="signed", actor="订单重导", source="import"))
    exc = DataException(source_table="orders", source_pk=str(o.id),
                        exception_type="order_missing_alipay", severity="warning",
                        description="订单 G-NEW2 已成交却无任何收款凭据", status="open")
    db_session.add(exc); db_session.commit()
    assert er.recheck(db_session, exc) is None
    assert er.bulk_close_resolved(db_session, types=["order_missing_alipay"]) == {
        "order_missing_alipay": 1,
    }
    assert exc.status == "resolved"


def test_customer_confirmation_starts_three_day_grace(db_session):
    _order(db_session, "G-CONFIRM-NEW", ship_date=date.today() - timedelta(days=40),
           customer_confirmed_date=date.today() - timedelta(days=2))
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert not _excs(db_session, "G-CONFIRM-NEW")


def test_old_logistics_signoff_without_customer_confirmation_not_flagged(db_session):
    _order(db_session, "G-LOGISTICS-OLD", ship_date=date.today() - timedelta(days=50),
           tracking_confirmed=True)
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert not _excs(db_session, "G-LOGISTICS-OLD")


def test_zero_paid_full_refund_not_flagged_and_rechecker_closes(db_session):
    """新版淘宝全退表达：实付=0、退款≈应付，也不应要求收款凭据。"""
    order = _order(
        db_session,
        "G-FULL-REFUND",
        paid_amount=D("0"),
        buyer_payable_amount=D("1928.57"),
        refund_amount=D("1928.57"),
        ship_date=date.today() - timedelta(days=40),
    )
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert not _excs(db_session, "G-FULL-REFUND")

    exc = DataException(
        source_table="orders",
        source_pk=str(order.id),
        exception_type="order_missing_alipay",
        severity="warning",
        description="订单 G-FULL-REFUND 已成交却无任何收款凭据",
        status="open",
    )
    db_session.add(exc)
    db_session.commit()
    assert er.recheck(db_session, exc) is None

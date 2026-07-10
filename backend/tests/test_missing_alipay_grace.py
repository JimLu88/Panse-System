# -*- coding: utf-8 -*-
"""缺收款凭据·签收宽限 (2026-07-10 治本"当天签收必空报"):
凭据到账节奏跟签收日走(签收→打款→流水T+1~2), 刚签收/刚发货的单暂不报; 老单真缺的照报。"""
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
    """老签收单(无近签收记录/发货日久远)无任何凭据 → 照报, 宽限不放水。"""
    _order(db_session, "G-OLD1", ship_date=date.today() - timedelta(days=40))
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert _excs(db_session, "G-OLD1")


def test_recently_signed_not_flagged(db_session):
    """近5天内才转 signed(状态改动档案可查) → 打款/流水还在途, 不报。"""
    _order(db_session, "G-NEW1", ship_date=date.today() - timedelta(days=40))
    db_session.add(FieldChange(table_name="orders", row_pk="G-NEW1", field="status",
                               old_value="paid", new_value="signed", actor="订单重导", source="import"))
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert not _excs(db_session, "G-NEW1")


def test_recent_shipdate_not_flagged(db_session):
    """发货日近12天内(发货→签收→打款在途) → 不报。"""
    _order(db_session, "G-SHIP1", ship_date=date.today() - timedelta(days=5))
    db_session.commit()
    dq.scan_order_missing_alipay(db_session)
    db_session.commit()
    assert not _excs(db_session, "G-SHIP1")


def test_checker_closes_recently_signed(db_session):
    """复核器同口径: 已存在的空报, 该单是近签收 → 复核 None(可销账)。"""
    o = _order(db_session, "G-NEW2", ship_date=date.today() - timedelta(days=40))
    db_session.flush()
    db_session.add(FieldChange(table_name="orders", row_pk="G-NEW2", field="status",
                               old_value="paid", new_value="signed", actor="订单重导", source="import"))
    exc = DataException(source_table="orders", source_pk=str(o.id),
                        exception_type="order_missing_alipay", severity="warning",
                        description="订单 G-NEW2 已成交却无任何收款凭据", status="open")
    db_session.add(exc); db_session.commit()
    assert er.recheck(db_session, exc) is None

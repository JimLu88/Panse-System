"""Task 11: 定制订单缺成本依据 → 挂异常 (data_quality_service.scan_custom_order_missing_cost_basis)。"""
from __future__ import annotations

from decimal import Decimal

from app.models.exception import DataException
from app.models.order import Order
from app.services import data_quality_service


def _custom(db, no, **kw):
    o = Order(platform="淘宝", order_no=no, is_custom=True, sku_code="S1", **kw)
    db.add(o)
    db.commit()
    return o


def _scan(db):
    n = data_quality_service.scan_custom_order_missing_cost_basis(db)
    db.commit()
    return n


def test_flags_custom_without_cost_basis(db_session):
    _custom(db_session, "C1")   # 无 actual_cost, 无 custom_surcharge
    assert _scan(db_session) == 1
    rows = db_session.query(DataException).filter_by(
        exception_type="custom_order_missing_cost_basis").all()
    assert len(rows) == 1


def test_skips_custom_with_actual_cost(db_session):
    _custom(db_session, "C2", actual_cost=Decimal("100"))
    assert _scan(db_session) == 0


def test_skips_custom_with_surcharge(db_session):
    _custom(db_session, "C3", custom_surcharge=Decimal("50"))
    assert _scan(db_session) == 0


def test_skips_non_custom(db_session):
    db_session.add(Order(platform="淘宝", order_no="C4", is_custom=False, sku_code="S1"))
    db_session.commit()
    assert _scan(db_session) == 0


def test_idempotent_no_duplicate_exceptions(db_session):
    _custom(db_session, "C5")
    _scan(db_session)
    _scan(db_session)   # 第二次不应重复堆积
    rows = db_session.query(DataException).filter_by(
        exception_type="custom_order_missing_cost_basis").all()
    assert len(rows) == 1

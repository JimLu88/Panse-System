import pytest

from app.models.exception import DataException
from app.models.order import Order
from app.services import order_service


def _add(db, order_no, status="pending_payment"):
    o = Order(platform="淘宝", order_no=order_no, status=status, qty=1)
    db.add(o)
    db.flush()
    return o


def test_transition_pending_to_paid(db_session):
    o = _add(db_session, "X1")
    order_service.transition(db_session, o, "paid")
    assert o.status == "paid"


def test_transition_to_same_status_is_noop(db_session):
    o = _add(db_session, "X1")
    order_service.transition(db_session, o, "pending_payment")
    assert o.status == "pending_payment"


def test_transition_paid_to_shipped(db_session):
    o = _add(db_session, "X1", status="paid")
    order_service.transition(db_session, o, "shipped")
    assert o.status == "shipped"


def test_transition_shipped_to_signed(db_session):
    o = _add(db_session, "X1", status="shipped")
    order_service.transition(db_session, o, "signed")
    assert o.status == "signed"


def test_transition_invalid_raises(db_session):
    o = _add(db_session, "X1", status="pending_payment")
    with pytest.raises(order_service.InvalidStatusTransition):
        order_service.transition(db_session, o, "shipped")  # 必须先 paid


def test_transition_invalid_force_writes_exception(db_session):
    o = _add(db_session, "X1", status="pending_payment")
    order_service.transition(db_session, o, "shipped", actor="admin", force=True)
    assert o.status == "shipped"
    excs = db_session.query(DataException).all()
    assert len(excs) == 1
    assert excs[0].exception_type == "forced_status_transition"
    assert excs[0].context["actor"] == "admin"


def test_transition_from_cancelled_blocked(db_session):
    o = _add(db_session, "X1", status="cancelled")
    with pytest.raises(order_service.InvalidStatusTransition):
        order_service.transition(db_session, o, "paid")


def test_transition_aftersales_back_to_signed(db_session):
    o = _add(db_session, "X1", status="aftersales")
    order_service.transition(db_session, o, "signed")
    assert o.status == "signed"

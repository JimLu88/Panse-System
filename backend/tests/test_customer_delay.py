"""客户延期与远期单分离，并按客户确认的新日期计算逾期。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api.orders import ProductionPatch, factory_production, update_production
from app.models.order import Order
from app.services import order_flags


def _order(**kwargs) -> Order:
    values = {
        "order_no": "CUSTOMER-DELAY",
        "platform": "淘宝",
        "status": "paid",
        "qty": 1,
        "order_date": date.today() - timedelta(days=40),
        "ship_deadline": date.today() - timedelta(days=10),
    }
    values.update(kwargs)
    return Order(**values)


def test_customer_delay_uses_new_deadline_and_keeps_original(db_session):
    original = date.today() - timedelta(days=10)
    delayed_to = date.today() + timedelta(days=20)
    order = _order(
        ship_deadline=original,
        is_customer_delayed=True,
        customer_delay_deadline=delayed_to,
        production_note="客户要求延期发货",
        seller_memo="开始制作，注意打木架",
    )
    db_session.add(order)
    db_session.commit()

    card = next(x for x in factory_production(product=None, db=db_session) if x["id"] == order.id)
    assert card["is_customer_delayed"] is True
    assert card["original_deadline"] == original.isoformat()
    assert card["effective_deadline"] == delayed_to.isoformat()
    assert card["days_left"] == 20
    assert card["status"] == "normal"


def test_customer_delay_without_start_signal_stays_remote_after_deadline(db_session):
    order = _order(
        is_customer_delayed=True,
        customer_delay_deadline=date.today() - timedelta(days=1),
    )
    db_session.add(order)
    db_session.commit()

    card = next(x for x in factory_production(product=None, db=db_session) if x["id"] == order.id)
    assert card["effective_deadline"] is None
    assert card["days_left"] is None
    assert card["status"] == "remote"


def test_customer_delay_waits_for_exact_start_signal():
    order = _order(
        is_customer_delayed=True,
        customer_delay_deadline=date.today() + timedelta(days=20),
        production_note="客户要求延期发货",
    )
    assert order_flags.is_remote(order) is True
    assert order_flags.is_factory_remote(order) is True

    order.production_note = "可以制作，预计月底发货"
    assert order_flags.is_remote(order) is True
    assert order_flags.is_factory_remote(order) is True

    order.production_note = "客户已通知开始制作"
    assert order_flags.is_remote(order) is False
    assert order_flags.is_factory_remote(order) is False


def test_update_customer_delay_requires_date_and_is_mutually_exclusive(db_session):
    order = _order(order_no="UPDATE-DELAY", is_remote_ship=True)
    db_session.add(order)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        update_production(order.id, ProductionPatch(is_customer_delayed=True), db_session)
    assert exc.value.status_code == 422

    delayed_to = date.today() + timedelta(days=15)
    update_production(
        order.id,
        ProductionPatch(is_customer_delayed=True, customer_delay_deadline=delayed_to),
        db_session,
    )
    db_session.refresh(order)
    assert order.is_customer_delayed is True
    assert order.customer_delay_deadline == delayed_to
    assert order.is_remote_ship is False
    assert order_flags.is_remote(order) is True

    update_production(order.id, ProductionPatch(is_remote_ship=True), db_session)
    db_session.refresh(order)
    assert order.is_remote_ship is True
    assert order.is_customer_delayed is False
    assert order.customer_delay_deadline is None

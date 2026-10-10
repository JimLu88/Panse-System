"""Factory cards keep child factory identities without mutating business data."""
from datetime import date

from app.api.orders import factory_production
from app.models.order import Order, OrderDetail


def seed(db, *, imported=True):
    order = Order(order_no="3309085849115067896", platform="淘宝", status="paid", qty=1,
                  order_date=date.today(), product_name="榉木床头柜", sku="标准",
                  product_code="BEECH", sku_code="BEECH-1", is_customer_delayed=True)
    db.add(order)
    if imported:
        db.add(OrderDetail(order_no=order.order_no, sub_order_no="child-162", source="import",
                           factory_delivery_required=True, factory_no=162,
                           line_status="paid", qty=1, sku_code="BEECH-1"))
    db.commit()
    return order


def test_order_child_factory_and_product_search(db_session):
    order = seed(db_session)
    for query in (order.order_no, "  " + order.order_no + "  ", "child-162", "畔色162单",
                  "162", "榉木床头柜", "BEECH-1", None):
        cards = factory_production(product=query, db=db_session)
        assert len(cards) == 1, query
        assert cards[0]["factory_no"] == 162
        assert cards[0]["factory_nos"] == [162]
        assert cards[0]["order_label"] == "畔色162单"
        assert cards[0]["is_customer_delayed"] is True
    assert factory_production(product="not-an-order", db=db_session) == []
    assert not db_session.dirty
    db_session.refresh(order)
    assert order.factory_no is None


def test_multiple_children_keep_all_numbers_and_ignore_invalid_lines(db_session):
    order = seed(db_session)
    order.factory_no = 999  # stale parent identity must not override children
    for sub, number, state, required in [
        ("child-163", 163, "paid", True),
        ("refunded", 164, "cancelled", True),
        ("shipped", 165, "shipped", True),
        ("service", 166, "paid", False),
    ]:
        db_session.add(OrderDetail(order_no=order.order_no, sub_order_no=sub, source="import",
                                   factory_delivery_required=required, factory_no=number,
                                   line_status=state, qty=1, sku_code=sub))
    db_session.commit()
    card = factory_production(product="child-163", db=db_session)[0]
    assert card["factory_nos"] == [162, 163]
    assert card["factory_no"] is None
    assert card["order_label"] == "畔色162单、畔色163单"
    for q in ("畔色164单", "畔色165单", "畔色166单", "畔色999单"):
        assert factory_production(product=q, db=db_session) == []


def test_filtered_imports_do_not_restore_stale_parent_number(db_session):
    order = seed(db_session)
    order.factory_no = 999
    line = db_session.query(OrderDetail).one()
    line.refund_status = "退款成功"
    db_session.commit()
    card = factory_production(product=order.order_no, db=db_session)[0]
    assert card["factory_nos"] == []
    assert card["factory_no"] is None
    assert "999" not in card["order_label"]


def test_legacy_parent_and_shipped_order_visibility(db_session):
    order = seed(db_session, imported=False)
    order.factory_no = 162
    db_session.commit()
    assert factory_production(product="畔色162单", db=db_session)[0]["factory_no"] == 162
    order.status = "shipped"
    db_session.commit()
    assert factory_production(product=order.order_no, db=db_session) == []


def test_master_summary_is_not_an_extra_factory_number(db_session):
    order = seed(db_session)
    db_session.add(OrderDetail(order_no=order.order_no, sub_order_no=order.order_no, source="import",
                              factory_delivery_required=True, factory_no=999, line_status="paid"))
    db_session.commit()
    assert factory_production(product=None, db=db_session)[0]["factory_nos"] == [162]

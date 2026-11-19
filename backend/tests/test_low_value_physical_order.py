from decimal import Decimal

import pytest
from app.models.order import Order, OrderDetail
from app.services import factory_production_evidence as facts
from app.services import order_sheet_archive_service as sheets
from app.services import order_line_delivery_service as delivery


@pytest.fixture
def item(db_session):
    o = Order(order_no='LOW-1', platform='淘宝', status='paid', qty=2,
              paid_amount=Decimal('285.52'), product_name='组合柜木头底座')
    l = OrderDetail(order_no='LOW-1', sub_order_no='LOW-1', source='import',
                    sync_key='line:LOW-1', sku_code='BASE', sku_name='木头底座',
                    product_name='木头底座', qty=2, line_status='paid',
                    factory_delivery_required=True)
    db_session.add_all([o, l]); db_session.commit()
    return o, l


def confirm(db):
    return facts.confirm_low_value_physical_order(db, order_no='LOW-1', expected_sku='BASE',
        expected_qty=2, expected_amount='285.52', actor='explicit-user', evidence_ref='request:1')


def test_only_exact_confirmed_order_bypasses_amount_gate(db_session, item):
    o, l = item
    assert sheets._is_parts_topup(db_session, o)[0]
    assert not delivery.line_is_factory_eligible(db_session, l, o)
    confirm(db_session)
    assert not sheets._is_parts_topup(db_session, o)[0]
    assert delivery.line_is_factory_eligible(db_session, l, o)
    other = Order(order_no='OTHER', platform='淘宝', status='paid', qty=2, paid_amount=285.52)
    db_session.add(other); db_session.commit()
    assert sheets._is_parts_topup(db_session, other)[0]
    assert o.paid_amount == Decimal('285.52') and o.qty == l.qty == 2


@pytest.mark.parametrize('change', ['sku', 'qty', 'amount', 'new_child'])
def test_changed_facts_invalidate_exception(db_session, item, change):
    o, l = item; confirm(db_session)
    if change == 'sku': l.sku_code = 'DIFFERENT'
    elif change == 'qty': l.qty = 3
    elif change == 'amount': o.paid_amount = Decimal('300')
    else: db_session.add(OrderDetail(order_no=o.order_no, sub_order_no='CHILD2',
        source='import', sync_key='line:CHILD2', sku_code='BASE', qty=1))
    db_session.commit()
    assert sheets._is_parts_topup(db_session, o)[0]


@pytest.mark.parametrize('guard', ['topup', 'remote', 'cancelled', 'refund'])
def test_exception_never_bypasses_other_production_gates(db_session, item, guard):
    o, l = item; confirm(db_session)
    if guard == 'topup': o.remark = '补差价'
    elif guard == 'remote': o.seller_memo = '等客户通知'
    elif guard == 'cancelled': o.status = 'cancelled'
    else: l.refund_status = '退款成功'; l.refund_amount = Decimal('285.52'); l.amount = Decimal('285.52')
    db_session.commit()
    assert not delivery.line_is_factory_eligible(db_session, l, o)


def test_confirmation_is_explicit_and_conflicts_not_overwritten(db_session, item):
    with pytest.raises(ValueError):
        facts.confirm_low_value_physical_order(db_session, order_no='LOW-1', expected_sku='WRONG',
            expected_qty=2, expected_amount='285.52', actor='user', evidence_ref='request:1')
    first = confirm(db_session)
    assert confirm(db_session) == first
    with pytest.raises(ValueError):
        facts.confirm_low_value_physical_order(db_session, order_no='LOW-1', expected_sku='BASE',
            expected_qty=2, expected_amount='285.52', actor='someone-else', evidence_ref='request:2')

from datetime import date
from decimal import Decimal

from app.models.marketing import PromotionFlow
from app.models.order import Order
from app.services import roi_service


def test_empty_state(db_session):
    r = roi_service.compute(db_session)
    assert r.promotion_spend == Decimal("0")
    assert r.order_count == 0
    assert r.roi is None  # divide by zero避免


def test_basic_roi(db_session):
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 1), flow_type="支出", amount=Decimal("1000")))
    db_session.add(Order(platform="淘宝", order_no="O1", qty=1, paid_amount=Decimal("3000"), status="paid"))
    db_session.add(Order(platform="淘宝", order_no="O2", qty=1, paid_amount=Decimal("2000"), status="paid"))
    db_session.flush()
    r = roi_service.compute(db_session)
    # ROI = (5000 - 1000) / 1000 = 4.0
    assert r.promotion_spend == Decimal("1000")
    assert r.order_count == 2
    assert r.order_revenue == Decimal("5000")
    assert r.avg_order_value == Decimal("2500.00")
    assert r.roi == Decimal("4.0000")


def test_cancelled_orders_excluded(db_session):
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 1), flow_type="支出", amount=Decimal("500")))
    db_session.add(Order(platform="淘宝", order_no="O1", qty=1, paid_amount=Decimal("1000"), status="paid"))
    db_session.add(Order(platform="淘宝", order_no="O2", qty=1, paid_amount=Decimal("9999"), status="cancelled"))
    db_session.flush()
    r = roi_service.compute(db_session)
    assert r.order_count == 1
    assert r.order_revenue == Decimal("1000")


def test_period_filter(db_session):
    db_session.add(PromotionFlow(transaction_date=date(2026, 4, 1), flow_type="支出", amount=Decimal("500")))
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 1), flow_type="支出", amount=Decimal("1000")))
    db_session.add(Order(platform="淘宝", order_no="O1", order_date=date(2026, 4, 15), qty=1, paid_amount=Decimal("2000"), status="paid"))
    db_session.add(Order(platform="淘宝", order_no="O2", order_date=date(2026, 5, 15), qty=1, paid_amount=Decimal("3000"), status="paid"))
    db_session.flush()
    r = roi_service.compute(db_session, period_start=date(2026, 5, 1))
    assert r.promotion_spend == Decimal("1000")
    assert r.order_revenue == Decimal("3000")


def test_recharge_separate_from_spend(db_session):
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 1), flow_type="充值", amount=Decimal("5000")))
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 5), flow_type="支出", amount=Decimal("3000")))
    db_session.flush()
    r = roi_service.compute(db_session)
    assert r.promotion_recharge == Decimal("5000")
    assert r.promotion_spend == Decimal("3000")

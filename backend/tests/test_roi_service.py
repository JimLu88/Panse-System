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


def test_compute_excludes_refill(db_session):
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 1), flow_type="支出", amount=Decimal("100")))
    db_session.add(Order(platform="淘宝", order_no="O1", qty=1, paid_amount=Decimal("1000"), status="paid"))
    db_session.add(Order(platform="淘宝", order_no="R1", qty=1, paid_amount=Decimal("8888"),
                         status="signed", is_refill=True))   # 补单 → 不计入销售额
    db_session.flush()
    r = roi_service.compute(db_session)
    assert r.order_count == 1
    assert r.order_revenue == Decimal("1000")


def test_monthly_breakdown(db_session):
    # 推广支出: 4月500 / 5月1000
    db_session.add(PromotionFlow(transaction_date=date(2026, 4, 3), flow_type="支出", amount=Decimal("500")))
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 9), flow_type="支出", amount=Decimal("1000")))
    db_session.add(PromotionFlow(transaction_date=date(2026, 5, 9), flow_type="充值", amount=Decimal("9999")))  # 充值不算支出
    # 正式销售: 4月2000 / 5月3000; 5月还有补单9999(剔除)+取消(剔除)
    db_session.add(Order(platform="淘宝", order_no="A", order_date=date(2026, 4, 15), paid_amount=Decimal("2000"), status="signed"))
    db_session.add(Order(platform="淘宝", order_no="B", order_date=date(2026, 5, 15), paid_amount=Decimal("3000"), status="paid"))
    db_session.add(Order(platform="淘宝", order_no="R", order_date=date(2026, 5, 16), paid_amount=Decimal("9999"), status="signed", is_refill=True))
    db_session.add(Order(platform="淘宝", order_no="X", order_date=date(2026, 5, 17), paid_amount=Decimal("5000"), status="cancelled"))
    db_session.flush()

    r = roi_service.monthly_breakdown(db_session)
    assert [m["period"] for m in r["months"]] == ["2026-05", "2026-04"]   # 降序
    may, apr = r["months"]
    assert may["promotion_spend"] == 1000.0 and may["order_revenue"] == 3000.0
    assert may["order_count"] == 1                                       # 补单/取消已剔除
    assert round(may["spend_ratio"], 4) == round(1000 / 3000, 4)         # 占比
    assert may["roi"] == 2.0                                             # (3000-1000)/1000
    assert apr["spend_ratio"] == 0.25                                    # 500/2000
    assert r["total_spend"] == 1500.0 and r["total_revenue"] == 5000.0
    assert r["overall_spend_ratio"] == 0.3                              # 1500/5000

    # 按年过滤
    assert roi_service.monthly_breakdown(db_session, year=2025)["months"] == []

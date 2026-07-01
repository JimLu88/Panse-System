"""现金流「待缴税费(季度)」减项 (用户 2026-07-01: 税费每季缴一次; 当季必扣, 上季手选已缴则不计)。"""
from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.services import cash_flow_service as cf


def _order(db, no, od, paid, **kw):
    o = Order(order_no=no, platform="淘宝", status="signed", order_date=od,
              paid_amount=Decimal(str(paid)), is_refill=False, is_historical=False, qty=1)
    for k, v in kw.items():
        setattr(o, k, v)
    db.add(o)
    db.commit()
    return o


def test_quarterly_tax_counts_all_when_none_paid(db_session):
    _order(db_session, "A1", date(2026, 2, 10), 10000)   # Q1
    _order(db_session, "A2", date(2026, 5, 10), 20000)   # Q2 (最新=当季)
    r = cf._quarterly_tax(db_session)
    qs = {q["quarter"]: q for q in r["quarters"]}
    assert qs["2026-Q1"]["tax"] == Decimal("200.00")     # 10000×2%
    assert qs["2026-Q2"]["tax"] == Decimal("400.00")     # 20000×2%
    assert r["current_quarter"] == "2026-Q2"
    assert qs["2026-Q2"]["is_current"] is True
    assert r["counted_total"] == Decimal("600.00")       # 都未标已缴 → 全计


def test_past_quarter_marked_paid_excluded(db_session):
    _order(db_session, "A1", date(2026, 2, 10), 10000)
    _order(db_session, "A2", date(2026, 5, 10), 20000)
    cf.update_manual(db_session, tax_paid_quarters=["2026-Q1"]); db_session.commit()
    r = cf._quarterly_tax(db_session)
    qs = {q["quarter"]: q for q in r["quarters"]}
    assert qs["2026-Q1"]["paid"] is True
    assert r["counted_total"] == Decimal("400.00")       # Q1已缴排除, 只剩当季 Q2


def test_current_quarter_cannot_be_marked_paid(db_session):
    _order(db_session, "A2", date(2026, 5, 10), 20000)   # Q2 = 当季
    cf.update_manual(db_session, tax_paid_quarters=["2026-Q2"]); db_session.commit()  # 试图标当季已缴
    r = cf._quarterly_tax(db_session)
    qs = {q["quarter"]: q for q in r["quarters"]}
    assert qs["2026-Q2"]["is_current"] is True
    assert qs["2026-Q2"]["paid"] is False                # 当季不可标已缴
    assert r["counted_total"] == Decimal("400.00")       # 当季恒计


def test_full_refund_excluded_from_tax(db_session):
    _order(db_session, "A3", date(2026, 5, 10), 10000, refund_amount=Decimal("10000"), status="refunded")
    r = cf._quarterly_tax(db_session)
    assert r["counted_total"] == Decimal("0.00")         # 全退单不成交 → 无税


def test_summary_has_tax_subtraction(db_session):
    _order(db_session, "A2", date(2026, 5, 10), 20000)
    s = cf.compute_summary(db_session)
    tax_line = next((x for x in s["subtractions"] if x["key"] == "tax_quarterly"), None)
    assert tax_line is not None
    assert tax_line["amount"] == Decimal("400.00")
    assert s["manual"]["tax_current_quarter"] == "2026-Q2"
    assert any(q["quarter"] == "2026-Q2" for q in s["manual"]["tax_quarters"])

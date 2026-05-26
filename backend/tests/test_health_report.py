from datetime import date
from decimal import Decimal

import pytest

from app.models.exception import DataException
from app.models.order import Order
from app.services import health_report


def test_invalid_month_rejected(db_session):
    with pytest.raises(ValueError):
        health_report.generate(db_session, 2026, 13)


def test_empty_state(db_session):
    r = health_report.generate(db_session, 2026, 5)
    assert r.period_start == date(2026, 5, 1)
    assert r.period_end == date(2026, 5, 31)
    assert r.exceptions["total_open"] == 0
    assert r.orders["month_count"] == 0
    assert r.integrity_score == 100


def test_exceptions_aggregated_by_severity_and_type(db_session):
    for sev in ["info", "info", "warning", "warning", "error"]:
        db_session.add(DataException(
            source_table="x", source_pk="y", exception_type="missing_field",
            severity=sev, description="d", status="open",
        ))
    db_session.flush()
    r = health_report.generate(db_session, 2026, 5)
    assert r.exceptions["total_open"] == 5
    assert r.exceptions["by_severity"] == {"info": 2, "warning": 2, "error": 1}
    assert r.exceptions["top_types"] == {"missing_field": 5}


def test_score_drops_with_exceptions(db_session):
    for i in range(40):
        db_session.add(DataException(
            source_table="x", source_pk=f"y{i}", exception_type="t",
            severity="warning", description="d", status="open",
        ))
    db_session.flush()
    r = health_report.generate(db_session, 2026, 5)
    # 40 条 → -4 分
    assert r.integrity_score == 96


def test_orders_in_month(db_session):
    db_session.add(Order(
        platform="淘宝", order_no="O1", order_date=date(2026, 5, 10),
        qty=1, paid_amount=Decimal("1000"), status="paid",
    ))
    db_session.add(Order(
        platform="淘宝", order_no="O2", order_date=date(2026, 4, 10),  # 上月
        qty=1, paid_amount=Decimal("99999"), status="paid",
    ))
    db_session.add(Order(  # 取消的不计
        platform="淘宝", order_no="O3", order_date=date(2026, 5, 11),
        qty=1, paid_amount=Decimal("500"), status="cancelled",
    ))
    db_session.flush()
    r = health_report.generate(db_session, 2026, 5)
    assert r.orders["month_count"] == 1
    assert r.orders["month_revenue"] == "1000.00"


def test_headlines_summarize_state(db_session):
    db_session.add(Order(
        platform="淘宝", order_no="O1", order_date=date(2026, 5, 10),
        qty=1, paid_amount=Decimal("1000"), status="paid",
    ))
    db_session.flush()
    r = health_report.generate(db_session, 2026, 5)
    assert any("1 单" in h for h in r.headlines)


def test_to_dict_serializes_dates(db_session):
    r = health_report.generate(db_session, 2026, 5)
    d = health_report.to_dict(r)
    assert d["period_start"] == "2026-05-01"
    assert d["period_end"] == "2026-05-31"
    assert "integrity_score" in d

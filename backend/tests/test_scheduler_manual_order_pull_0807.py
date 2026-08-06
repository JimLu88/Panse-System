from datetime import datetime

from app.services import scheduler


def test_order_pull_freshness_gate_is_current_hour_before_evening():
    assert scheduler._order_pull_freshness_hour(datetime(2026, 8, 7, 9, 32)) == 9


def test_order_pull_freshness_gate_stays_at_18_after_evening():
    assert scheduler._order_pull_freshness_hour(datetime(2026, 8, 7, 21, 4)) == 18

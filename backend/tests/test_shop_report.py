"""Task 7: 分店统计 shop_report_service.compute_shop_stats。"""
from __future__ import annotations

from datetime import date

from app.models.order import Order
from app.services import shop_report_service


def _order(db, no, shop, qty, paid, status="signed", refill=False, d=None):
    db.add(Order(platform="淘宝", order_no=no, shop=shop, qty=qty,
                 paid_amount=paid, status=status, is_refill=refill, order_date=d))
    db.commit()


def test_shop_stats_aggregates_and_sorts(db_session):
    _order(db_session, "A1", "畔色店", 2, 100)
    _order(db_session, "A2", "畔色店", 1, 50)
    _order(db_session, "B1", "孚格店", 3, 300)
    _order(db_session, "C1", None, 1, 10)              # 未归属
    _order(db_session, "X1", "畔色店", 5, 999, status="cancelled")  # 排除
    res = shop_report_service.compute_shop_stats(db_session)
    by = {r["shop"]: r for r in res}
    assert by["畔色店"]["order_count"] == 2
    assert by["畔色店"]["total_qty"] == 3
    assert by["畔色店"]["total_revenue"] == 150.0
    assert by["孚格店"]["total_revenue"] == 300.0
    assert by["未归属"]["order_count"] == 1
    assert res[0]["shop"] == "孚格店"   # 降序: 300 在 150 前


def test_shop_stats_excludes_refill_by_default(db_session):
    _order(db_session, "R1", "畔色店", 1, 100, refill=True)
    assert shop_report_service.compute_shop_stats(db_session) == []


def test_shop_stats_date_range(db_session):
    _order(db_session, "D1", "畔色店", 1, 100, d=date(2026, 1, 1))
    _order(db_session, "D2", "畔色店", 1, 100, d=date(2026, 6, 1))
    res = shop_report_service.compute_shop_stats(
        db_session, start=date(2026, 5, 1), end=date(2026, 12, 31)
    )
    assert len(res) == 1 and res[0]["order_count"] == 1

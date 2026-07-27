"""取数体检任务 (daily_2000_ingest_health): 已注册 + 今日无新订单必报, 有今日订单则不报。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.models.order import Order
from app.services import scheduler, settings_service, web_agent_service


def test_health_job_registered():
    scheduler._register_default_jobs()
    ids = {j["job_id"] for j in scheduler.list_jobs()}
    assert "daily_2000_ingest_health" in ids


def test_health_flags_no_new_orders(db_session, monkeypatch):
    # 隔离: 探活按在线算, 这样唯一应触发的问题是"今日无新订单"
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})
    res = scheduler._job_ingest_health_check(db_session)
    assert res["new_orders_today"] == 0
    assert any("今日无新订单" in p for p in res["problems"])
    # conftest 设 PANSE_DISABLE_NOTIFY=1 → 绝不真推, 只标记 disabled
    assert res["pushed"] == ["disabled"]


def test_health_quiet_when_order_created_today(db_session, monkeypatch):
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})
    db_session.add(Order(
        platform="淘宝", order_no="O-today", paid_amount=Decimal("100"),
        order_date=date.today(), status="signed", created_at=datetime.now(),
    ))
    db_session.commit()
    res = scheduler._job_ingest_health_check(db_session)
    assert res["new_orders_today"] >= 1
    assert not any("今日无新订单" in p for p in res["problems"])


def test_health_never_reports_password_expired_by_age(db_session, monkeypatch):
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})
    settings_service.set_value(db_session, "taobao_shipping_pwd_latest", "example-password")
    settings_service.set_value(
        db_session,
        "taobao_shipping_pwd_at",
        (datetime.now() - timedelta(days=30)).isoformat(),
    )
    db_session.add(Order(
        platform="淘宝", order_no="O-old-password", paid_amount=Decimal("100"),
        order_date=date.today(), status="signed", created_at=datetime.now(),
    ))
    db_session.commit()

    res = scheduler._job_ingest_health_check(db_session)

    assert not any("口令已过期" in p for p in res["problems"])
    assert res["pending_shipping_password_files"] == []

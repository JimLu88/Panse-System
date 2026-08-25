"""取数体检任务：核对刷新证据与唤醒桥，不把“没有新增订单”误报成失败。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.models.order import Order
from app.services import (
    agent_ingest_service,
    notify_service,
    order_sheet_archive_service,
    scheduler,
    settings_service,
    web_agent_service,
    web_agent_wake_service,
)


def _healthy_runtime(monkeypatch, *, fresh: bool = True):
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": False})
    monkeypatch.setattr(
        web_agent_wake_service,
        "status",
        lambda db: {"bridge": {"last_seen_at": datetime.now().astimezone().isoformat()}},
    )
    monkeypatch.setattr(
        agent_ingest_service,
        "order_data_fresh",
        lambda db, **kwargs: fresh,
    )


def test_health_job_registered():
    scheduler._register_default_jobs()
    ids = {j["job_id"] for j in scheduler.list_jobs()}
    assert "daily_2000_ingest_health" in ids


def test_health_quiet_when_no_new_orders_but_snapshot_is_fresh(db_session, monkeypatch):
    _healthy_runtime(monkeypatch, fresh=True)
    res = scheduler._job_ingest_health_check(db_session)
    assert res["new_orders_today"] == 0
    assert res["agent_online"] is False
    assert res["wake_bridge_online"] is True
    assert res["problems"] == []
    assert res["pushed"] == []


def test_health_quiet_when_order_created_today(db_session, monkeypatch):
    _healthy_runtime(monkeypatch, fresh=True)
    db_session.add(Order(
        platform="淘宝", order_no="O-today", paid_amount=Decimal("100"),
        order_date=date.today(), status="signed", created_at=datetime.now(),
    ))
    db_session.commit()
    res = scheduler._job_ingest_health_check(db_session)
    assert res["new_orders_today"] >= 1
    assert not any("今日无新订单" in p for p in res["problems"])


def test_health_flags_missing_evening_snapshot(db_session, monkeypatch):
    _healthy_runtime(monkeypatch, fresh=False)
    res = scheduler._job_ingest_health_check(db_session)
    assert any("18:00后订单数据未刷新" in p for p in res["problems"])
    assert res["pushed"] == ["disabled"]


def test_health_problem_only_uses_alert_route_and_never_posts_order_group(db_session, monkeypatch):
    _healthy_runtime(monkeypatch, fresh=False)
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    pushed = []
    monkeypatch.setattr(
        notify_service,
        "notify",
        lambda db, text, **kwargs: pushed.append((text, kwargs)) or (True, "sent"),
    )
    monkeypatch.setattr(
        notify_service,
        "broadcast_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不得广播到飞书订单群")),
    )
    monkeypatch.setattr(
        order_sheet_archive_service,
        "send_order_update_complete_notice",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("取数失败/处理中不得向订单群发送完成回执")
        ),
    )

    res = scheduler._job_ingest_health_check(db_session)

    assert res["pushed"] == ["wechat_push"]
    assert pushed[0][1]["wechat_allowed"] is True
    assert pushed[0][1]["title"] == "畔色 ERP | 取数体检异常"
    assert "update_complete_notice" not in res


def test_health_never_reports_password_expired_by_age(db_session, monkeypatch):
    _healthy_runtime(monkeypatch, fresh=True)
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

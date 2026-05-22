"""Phase 1A: 调度器 + ScheduledJobRun 日志."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.scheduled_job import ScheduledJobRun
from app.services import scheduler as scheduler_service


def test_register_and_list_jobs(db_session, monkeypatch):
    scheduler_service._REGISTRY.clear()
    scheduler_service.register_job(
        "test_job", "测试任务",
        lambda s: {"ok": True}, interval_minutes=10,
    )
    jobs = scheduler_service.list_jobs()
    assert any(j["job_id"] == "test_job" for j in jobs)
    info = next(j for j in jobs if j["job_id"] == "test_job")
    assert info["label"] == "测试任务"
    assert info["kind"] == "interval"


def test_trigger_now_runs_inline_when_scheduler_off(monkeypatch):
    """没启动调度器时, trigger_now 直接同步跑."""
    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None
    called = []
    def fn(s):
        called.append(1)
        return {"ran": 1}
    scheduler_service.register_job("immediate", "立即", fn, interval_minutes=1)
    ok = scheduler_service.trigger_now("immediate")
    assert ok is True
    assert called == [1]


def test_trigger_writes_run_log(db_session, monkeypatch):
    """跑完一次任务应该有 ScheduledJobRun 记录."""
    # 把 SessionLocal 临时替换成测试用 sessionmaker
    from app import database as db_module
    # 找现存的 SessionLocal — 直接 hack: 把它换成给 db_session 用的 sessionmaker

    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None

    def fn(s):
        return {"items": 3}

    scheduler_service.register_job("test_log_job", "测试日志", fn, interval_minutes=1)

    # monkeypatch SessionLocal 用 db_session 的 bind
    Session = type(db_session)
    bind = db_session.get_bind()
    from sqlalchemy.orm import sessionmaker
    LocalSm = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "SessionLocal", LocalSm)
    # scheduler 内部 import 的 SessionLocal 也得指到这

    scheduler_service.trigger_now("test_log_job")

    # 查 ScheduledJobRun
    rows = db_session.query(ScheduledJobRun).all()
    assert any(r.job_id == "test_log_job" and r.status == "ok" for r in rows)


def test_scheduler_failure_logged(db_session, monkeypatch):
    from app import database as db_module
    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None

    def boom(s):
        raise RuntimeError("simulated")

    scheduler_service.register_job("fail_job", "失败任务", boom, interval_minutes=1)
    bind = db_session.get_bind()
    from sqlalchemy.orm import sessionmaker
    LocalSm = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "SessionLocal", LocalSm)
    scheduler_service.trigger_now("fail_job")

    rows = db_session.query(ScheduledJobRun).filter(ScheduledJobRun.job_id == "fail_job").all()
    assert len(rows) == 1
    assert rows[0].status == "fail"
    assert "simulated" in (rows[0].error or "")


def test_trigger_returns_false_for_unknown_job():
    scheduler_service._REGISTRY.clear()
    assert scheduler_service.trigger_now("nope") is False


# ----------------------------- API 端到端 ----------------------- #


def test_scheduler_api_list_and_trigger(monkeypatch):
    """GET /jobs + GET /runs + POST trigger."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import get_db
    from app import database as db_module
    from app.main import app
    from app.models import Base
    from app.services import auth_service

    engine = create_engine("sqlite:///:memory:", future=True,
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s = Sess()
    admin = auth_service.create_user(s, username="admin", password="x", role="admin",
                                     display_name="A")
    s.commit()
    token = auth_service.create_token(user_id=admin.id, username=admin.username, role="admin")
    s.close()

    monkeypatch.setattr(db_module, "SessionLocal", Sess)
    def override():
        ses = Sess()
        try: yield ses
        finally: ses.close()
    app.dependency_overrides[get_db] = override

    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None
    scheduler_service.register_job(
        "ping", "ping 任务", lambda s: {"pong": 1}, interval_minutes=5,
    )

    try:
        client = TestClient(app)
        h = {"Authorization": f"Bearer {token}"}
        r = client.get("/api/scheduler/jobs", headers=h)
        assert r.status_code == 200, r.text
        assert any(j["job_id"] == "ping" for j in r.json())

        r = client.post("/api/scheduler/jobs/ping/trigger", headers=h)
        assert r.status_code == 200
        assert r.json()["accepted"] is True

        r = client.get("/api/scheduler/runs", headers=h)
        runs = r.json()
        assert any(run["job_id"] == "ping" and run["status"] == "ok" for run in runs)

        r = client.post("/api/scheduler/jobs/nope/trigger", headers=h)
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_activate_future_orders_job(db_session, monkeypatch):
    """daily_08_activate_future 任务: activate_at <= now 的订单激活."""
    from app import database as db_module
    from app.models.order import Order
    from datetime import datetime as _dt, timedelta as _td

    past = _dt.now(timezone.utc) - _td(hours=1)
    future = _dt.now(timezone.utc) + _td(hours=24)
    db_session.add_all([
        Order(platform="淘宝", order_no="A1", status="pending_payment", activate_at=past),
        Order(platform="淘宝", order_no="A2", status="pending_payment", activate_at=future),
        Order(platform="淘宝", order_no="A3", status="pending_payment"),
    ])
    db_session.commit()

    bind = db_session.get_bind()
    from sqlalchemy.orm import sessionmaker
    LocalSm = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "SessionLocal", LocalSm)

    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None
    scheduler_service.register_job(
        "activate", "激活", scheduler_service._job_activate_future_orders,
        cron={"hour": 8, "minute": 0},
    )
    scheduler_service.trigger_now("activate")

    db_session.expire_all()
    orders = {o.order_no: o for o in db_session.query(Order).all()}
    assert orders["A1"].status == "paid"
    assert orders["A1"].activate_at is None
    assert orders["A2"].status == "pending_payment"
    assert orders["A3"].status == "pending_payment"

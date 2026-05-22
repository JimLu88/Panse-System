"""Phase 1B: 告警中心 service + API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.alert import Alert
from app.services import alert_service


def test_upsert_creates_new(db_session):
    a = alert_service.upsert(
        db_session, kind="low_stock_part", severity="warn",
        title="缺货", body="物料 M001 缺 5",
        dedupe_key="low_stock_part:M001",
        related_url="/inventory/parts?code=M001",
    )
    assert a.id is not None
    assert a.resolved_at is None
    assert a.dedupe_key == "low_stock_part:M001"


def test_upsert_dedupes_active(db_session):
    a1 = alert_service.upsert(db_session, kind="x", severity="info",
                              title="t1", dedupe_key="k1")
    a2 = alert_service.upsert(db_session, kind="x", severity="info",
                              title="t1", dedupe_key="k1", body="updated")
    assert a1.id == a2.id
    assert a2.body == "updated"


def test_upsert_creates_new_after_resolve(db_session):
    """resolved 的 alert 不会被去重 — 新事件应新建一条."""
    a1 = alert_service.upsert(db_session, kind="x", severity="info",
                              title="t", dedupe_key="k")
    alert_service.resolve(db_session, a1.id)
    a2 = alert_service.upsert(db_session, kind="x", severity="info",
                              title="t", dedupe_key="k")
    assert a1.id != a2.id


def test_upsert_critical_pushes_notify(db_session):
    """critical 自动调 notify_service."""
    with patch("app.services.notify_service.notify",
               return_value=(True, "ok")) as mock:
        a = alert_service.upsert(
            db_session, kind="watchdog", severity="critical",
            title="DB 挂了", body="db_ping fail x3",
            dedupe_key="watchdog:db_ping",
        )
        mock.assert_called_once()
    assert a.notified_at is not None


def test_upsert_critical_only_notifies_once(db_session):
    """同一 dedupe alert 复用时, 不重复推 (notified_at 已设)."""
    with patch("app.services.notify_service.notify",
               return_value=(True, "ok")) as mock:
        alert_service.upsert(db_session, kind="x", severity="critical",
                             title="t", dedupe_key="k1")
        # 第 2 次应该复用第 1 次的 record, 不再推
        alert_service.upsert(db_session, kind="x", severity="critical",
                             title="t", dedupe_key="k1", body="new body")
        assert mock.call_count == 1


def test_resolve_marks_resolved(db_session):
    a = alert_service.upsert(db_session, kind="x", severity="info", title="t")
    r = alert_service.resolve(db_session, a.id, resolved_by="alice")
    assert r.resolved_at is not None
    assert r.resolved_by == "alice"


def test_resolve_by_dedupe(db_session):
    alert_service.upsert(db_session, kind="x", severity="info", title="t1", dedupe_key="k")
    a2 = Alert(kind="y", severity="info", title="t2", dedupe_key="k")
    db_session.add(a2); db_session.flush()
    n = alert_service.resolve_by_dedupe(db_session, "k")
    assert n == 2


def test_list_active_filters_resolved(db_session):
    a1 = alert_service.upsert(db_session, kind="x", severity="warn", title="t")
    a2 = alert_service.upsert(db_session, kind="y", severity="info", title="t")
    alert_service.resolve(db_session, a1.id)
    rows = alert_service.list_active(db_session)
    ids = {r.id for r in rows}
    assert a2.id in ids
    assert a1.id not in ids


def test_list_active_filters_auto_expired(db_session):
    """auto_resolve_until 已过期的应被过滤."""
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    a = Alert(kind="x", severity="info", title="t",
              auto_resolve_until=past)
    db_session.add(a); db_session.flush()
    rows = alert_service.list_active(db_session)
    assert a.id not in {r.id for r in rows}


def test_auto_expire(db_session):
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    a = Alert(kind="x", severity="info", title="t",
              auto_resolve_until=past)
    db_session.add(a); db_session.flush()
    n = alert_service.auto_expire(db_session)
    assert n == 1
    db_session.refresh(a)
    assert a.resolved_at is not None
    assert a.resolved_by == "auto_expire"


def test_count_by_severity(db_session):
    alert_service.upsert(db_session, kind="x", severity="info", title="t")
    alert_service.upsert(db_session, kind="x", severity="warn", title="t",
                          dedupe_key="w1")
    alert_service.upsert(db_session, kind="x", severity="warn", title="t",
                          dedupe_key="w2")
    alert_service.upsert(db_session, kind="x", severity="critical",
                          title="t", dedupe_key="c1", push_notify=False)
    counts = alert_service.count_unresolved_by_severity(db_session)
    assert counts == {"info": 1, "warn": 2, "critical": 1}


# ----------------------------- API 端到端 ----------------------- #


def _api_client():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import get_db
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
    def override():
        ses = Sess()
        try: yield ses
        finally: ses.close()
    app.dependency_overrides[get_db] = override
    return TestClient(app), token, Sess


def test_alerts_api_active_and_dismiss():
    client, token, Sess = _api_client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        # 造一条 alert
        s = Sess()
        a = Alert(kind="x", severity="warn", title="缺货", dedupe_key="k1")
        s.add(a); s.commit()
        alert_id = a.id
        s.close()

        r = client.get("/api/alerts/active", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert any(b["id"] == alert_id for b in body)

        r = client.get("/api/alerts/summary", headers=h)
        assert r.status_code == 200
        assert r.json()["warn"] >= 1

        r = client.post(f"/api/alerts/{alert_id}/dismiss", headers=h)
        assert r.status_code == 200
        assert r.json()["resolved_at"] is not None

        r = client.get("/api/alerts/active", headers=h)
        assert not any(b["id"] == alert_id for b in r.json())
    finally:
        from app.main import app
        app.dependency_overrides.clear()

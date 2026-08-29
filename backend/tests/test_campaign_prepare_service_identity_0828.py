"""Prepare-only machine identity, path scope, encryption and audit gates."""
from __future__ import annotations

from fastapi.testclient import TestClient
from datetime import datetime
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import dependencies, middleware
from app.cli import campaign_prepare as prepare_cli
from app.cli import campaign_refresh_evidence as refresh_cli
from app.cli import campaign_correct_official_exemptions as correction_cli
from app.cli import campaign_resume_super_reduce_plan7 as resume_plan7_cli
from app.database import get_db
from app.main import app
from app.models import Base
from app.models.auth import AuditLog
from app.models.campaign import CampaignPlan
from app.models.settings import SystemSetting
from app.services import settings_service


TOKEN = "campaign-prepare-only-test-token"


def _request(path: str):
    from starlette.requests import Request

    return Request({
        "type": "http", "method": "POST", "path": path,
        "headers": [], "client": ("10.0.0.8", 0), "query_string": b"",
    })


def _payload() -> dict:
    return {
        "workflow_key": "campaign:super-reduce:2026-09-01",
        "name": "2026-09-01超级立减更新窗口",
        "campaign_type": "super_reduce",
        "start_at": "2026-09-01T00:00:00+08:00",
        "end_at": "2026-09-01T23:59:59+08:00",
        "qn_campaign_title": "超级立减",
        "platform_activity_mode": "long_running_update",
        "platform_active_until": "2028-07-31T23:59:59+08:00",
        "official_all_store": True,
        "official_exempt_item_ids": [],
    }


def test_prepare_token_is_encrypted_and_exact_path_scoped(db_session, monkeypatch):
    settings_service.set_value(
        db_session, dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING, TOKEN)
    db_session.commit()

    row = db_session.execute(select(SystemSetting).where(
        SystemSetting.key == dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING
    )).scalar_one()
    assert row.is_secret is True
    assert row.value_plain is None
    assert TOKEN not in (row.value_encrypted or "")
    assert dependencies.machine_identity_for_key(
        TOKEN, db_session, path="/api/campaigns/prepare"
    ) == "service:campaign-prepare"
    assert dependencies.machine_identity_for_key(
        TOKEN, db_session, path="/api/campaigns/refresh-evidence"
    ) == "service:campaign-prepare"
    assert dependencies.machine_identity_for_key(
        TOKEN, db_session,
        path="/api/campaigns/correct-official-exemptions"
    ) == "service:campaign-prepare"
    assert dependencies.machine_identity_for_key(
        TOKEN, db_session,
        path="/api/campaigns/resume-super-reduce-plan7"
    ) == "service:campaign-prepare"
    assert dependencies.machine_identity_for_key(
        TOKEN, db_session, path="/api/campaigns/item-exclusions"
    ) is None
    assert dependencies.machine_identity_for_key(
        TOKEN, db_session, path="/api/campaigns/1/push-signup"
    ) is None

    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    assert dependencies.require_authenticated(
        _request("/api/campaigns/prepare"), authorization=None,
        x_api_key=TOKEN, db=db_session) is None


def test_container_cli_has_fixed_direct_endpoint(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"ok":true}'

    class Opener:
        @staticmethod
        def open(req, timeout):
            captured["url"] = req.full_url
            captured["token"] = req.get_header("X-api-key")
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(prepare_cli.request, "build_opener", lambda *_: Opener())
    status, body = prepare_cli.call_prepare(b'{"workflow_key":"campaign:test"}', token=TOKEN)

    assert status == 200 and body == b'{"ok":true}'
    assert captured == {
        "url": "http://127.0.0.1:8000/api/campaigns/prepare",
        "token": TOKEN,
        "timeout": 300,
    }


def test_evidence_cli_has_fixed_direct_endpoint(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"ok":true}'

    class Opener:
        @staticmethod
        def open(req, timeout):
            captured["url"] = req.full_url
            captured["token"] = req.get_header("X-api-key")
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(refresh_cli.request, "build_opener", lambda *_: Opener())
    status, body = refresh_cli.call_refresh(
        b'{"workflow_key":"campaign:test","plan_id":7}', token=TOKEN)

    assert status == 200 and body == b'{"ok":true}'
    assert captured == {
        "url": "http://127.0.0.1:8000/api/campaigns/refresh-evidence",
        "token": TOKEN,
        "timeout": 1200,
    }


def test_official_exemption_correction_cli_has_fixed_direct_endpoint(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"ok":true}'

    class Opener:
        @staticmethod
        def open(req, timeout):
            captured["url"] = req.full_url
            captured["token"] = req.get_header("X-api-key")
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        correction_cli.request, "build_opener", lambda *_: Opener())
    status, body = correction_cli.call_correction(
        b'{"workflow_key":"campaign:test","plan_id":7,'
        b'"expected_official_exempt_item_ids":[],'
        b'"official_exempt_item_ids":["805268708396"]}',
        token=TOKEN,
    )

    assert status == 200 and body == b'{"ok":true}'
    assert captured == {
        "url": (
            "http://127.0.0.1:8000/api/campaigns/"
            "correct-official-exemptions"
        ),
        "token": TOKEN,
        "timeout": 300,
    }


def test_plan7_resume_cli_has_fixed_direct_endpoint(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"ok":true}'

    class Opener:
        @staticmethod
        def open(req, timeout):
            captured["url"] = req.full_url
            captured["token"] = req.get_header("X-api-key")
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        resume_plan7_cli.request, "build_opener", lambda *_: Opener())
    payload = (
        b'{"workflow_key":"campaign:super-reduce:2026-09-01",'
        b'"plan_id":7,"expected_status":"alarmed",'
        b'"expected_scope_sha256":'
        b'"73d73f5e78d5f7149b4425f6c7e9909e9892f037d4859498e6dea26f0163b7a4"}'
    )
    status, body = resume_plan7_cli.call_resume(payload, token=TOKEN)

    assert status == 200 and body == b'{"ok":true}'
    assert captured == {
        "url": (
            "http://127.0.0.1:8000/api/campaigns/"
            "resume-super-reduce-plan7"
        ),
        "token": TOKEN,
        "timeout": 1200,
    }


def test_plan7_resume_endpoint_is_exact_scoped_and_audited(monkeypatch):
    from app.services import campaign_resume_service

    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True)
    with Session() as db:
        settings_service.set_value(
            db, dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING, TOKEN)
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    calls = []

    def fake_resume(_db, **kwargs):
        calls.append(kwargs)
        return {
            "ok": True, "plan_id": 7,
            "workflow_key": kwargs["workflow_key"],
            "execution_boundary": {
                "platform_write": False, "automatic_retry": False,
            },
        }

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(middleware, "SessionLocal", Session)
    monkeypatch.setattr(
        campaign_resume_service, "resume_super_reduce_plan7", fake_resume)
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    payload = {
        "workflow_key": "campaign:super-reduce:2026-09-01",
        "plan_id": 7,
        "expected_status": "alarmed",
        "expected_scope_sha256": campaign_resume_service.EXPECTED_SCOPE_SHA256,
    }
    try:
        client = TestClient(app)
        response = client.post(
            "/api/campaigns/resume-super-reduce-plan7",
            headers={"X-API-Key": TOKEN}, json=payload)
        forbidden_shape = client.post(
            "/api/campaigns/resume-super-reduce-plan7",
            headers={"X-API-Key": TOKEN},
            json={**payload, "plan_id": 8, "retry": True})

        assert response.status_code == 200, response.text
        assert response.json()["execution_boundary"]["platform_write"] is False
        assert calls == [{
            "workflow_key": "campaign:super-reduce:2026-09-01",
            "expected_plan_id": 7,
            "expected_status": "alarmed",
            "expected_scope_sha256": campaign_resume_service.EXPECTED_SCOPE_SHA256,
        }]
        assert forbidden_shape.status_code == 422
        with Session() as db:
            audits = db.execute(select(AuditLog).where(
                AuditLog.path == "/api/campaigns/resume-super-reduce-plan7"
            ).order_by(AuditLog.id)).scalars().all()
            assert [row.status_code for row in audits[-2:]] == [200, 422]
            assert all(
                row.username == "service:campaign-prepare"
                for row in audits[-2:])
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_official_exemption_correction_endpoint_is_scoped_and_audited(
        monkeypatch):
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True)
    with Session() as db:
        settings_service.set_value(
            db, dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING, TOKEN)
        db.add(CampaignPlan(
            id=7,
            workflow_key="campaign:super-reduce:2026-09-01",
            name="2026-09-01超级立减更新窗口",
            campaign_type="super_reduce", tier="mid",
            start_at=datetime(2026, 9, 1, 0, 0, 0),
            end_at=datetime(2026, 9, 1, 23, 59, 59),
            qn_campaign_title="超级立减", status="draft",
            remark="official_all_store=true; official_exempt_items=",
            platform_activity_mode="long_running_update",
            platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
        ))
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(middleware, "SessionLocal", Session)
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    try:
        client = TestClient(app)
        payload = {
            "workflow_key": "campaign:super-reduce:2026-09-01",
            "plan_id": 7,
            "expected_official_exempt_item_ids": [],
            "official_exempt_item_ids": ["805268708396"],
        }
        response = client.post(
            "/api/campaigns/correct-official-exemptions",
            headers={"X-API-Key": TOKEN}, json=payload)
        replay = client.post(
            "/api/campaigns/correct-official-exemptions",
            headers={"X-API-Key": TOKEN}, json=payload)
        forbidden_shape = client.post(
            "/api/campaigns/correct-official-exemptions",
            headers={"X-API-Key": TOKEN},
            json={**payload, "platform_campaign_id": "99999"})

        assert response.status_code == 200, response.text
        assert response.json()["changed"] is True
        assert response.json()["plan"]["remark"].endswith("805268708396")
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        assert forbidden_shape.status_code == 422
        with Session() as db:
            audits = db.execute(select(AuditLog).where(
                AuditLog.path == "/api/campaigns/correct-official-exemptions"
            ).order_by(AuditLog.id)).scalars().all()
            assert [row.status_code for row in audits[-3:]] == [200, 200, 422]
            assert all(
                row.username == "service:campaign-prepare"
                for row in audits[-3:])
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_official_exemption_correction_endpoint_accepts_unsubmitted_alarm(
        monkeypatch):
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True)
    with Session() as db:
        settings_service.set_value(
            db, dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING, TOKEN)
        db.add(CampaignPlan(
            id=8,
            workflow_key="campaign:super88:49462:49469",
            name="超级88现货", campaign_type="big88", tier="big",
            start_at=datetime(2026, 9, 6, 20, 0, 0),
            end_at=datetime(2026, 9, 13, 23, 59, 59),
            qn_campaign_title="26年淘宝9月超级88", status="alarmed",
            remark="official_all_store=true; official_exempt_items=",
            platform_activity_mode="fixed_window",
            platform_campaign_id="49462",
            platform_united_activity_id="49469",
            platform_sign_record_id="3527841611",
        ))
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(middleware, "SessionLocal", Session)
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    try:
        response = TestClient(app).post(
            "/api/campaigns/correct-official-exemptions",
            headers={"X-API-Key": TOKEN},
            json={
                "workflow_key": "campaign:super88:49462:49469",
                "plan_id": 8,
                "expected_official_exempt_item_ids": [],
                "official_exempt_item_ids": ["805268708396"],
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["changed"] is True
        assert body["submission_guard"]["alarmed"] is True
        assert body["execution_boundary"]["platform_write"] is False
        with Session() as db:
            audit = db.execute(select(AuditLog).where(
                AuditLog.path == "/api/campaigns/correct-official-exemptions"
            ).order_by(AuditLog.id.desc())).scalars().first()
            assert audit.status_code == 200
            assert audit.username == "service:campaign-prepare"
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_evidence_refresh_endpoint_is_scoped_audited_and_covers_both_modes(
        monkeypatch):
    from app.services import campaign_service

    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True)
    with Session() as db:
        settings_service.set_value(
            db, dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING, TOKEN)
        plans = [
            CampaignPlan(
                id=7, workflow_key="campaign:super-reduce:2026-09-01",
                name="2026-09-01超级立减更新窗口",
                campaign_type="super_reduce", tier="mid",
                start_at=datetime(2026, 9, 1, 0, 0, 0),
                end_at=datetime(2026, 9, 1, 23, 59, 59),
                qn_campaign_title="超级立减", status="draft",
                remark="official_all_store=true; official_exempt_items=",
                platform_activity_mode="long_running_update",
                platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
            ),
            CampaignPlan(
                id=8, workflow_key="campaign:super88:49462:49469",
                name="超级88现货", campaign_type="big88", tier="big",
                start_at=datetime(2026, 9, 6, 20, 0, 0),
                end_at=datetime(2026, 9, 13, 23, 59, 59),
                qn_campaign_title="26年淘宝9月超级88", status="draft",
                remark="official_all_store=true; official_exempt_items=",
                platform_activity_mode="fixed_window",
                platform_campaign_id="49462",
                platform_united_activity_id="49469",
            ),
        ]
        db.add_all(plans)
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    def fake_refresh(_db, plan):
        return {
            "ok": True,
            "rows": [],
            "floor_refresh": {
                "observed": 2,
                "source": f"test:plan={plan.id}",
                "scope": f"campaign_price_floor_evidence_v2_plan_{plan.id}",
            },
            "export_evidence": {
                "filename": f"plan-{plan.id}.xlsx",
                "size": 123,
                "sha256": f"sha256-plan-{plan.id}",
                "identity": campaign_service.campaign_identity(plan),
            },
        }

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(middleware, "SessionLocal", Session)
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        fake_refresh)
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    try:
        client = TestClient(app)
        for plan_id, workflow_key in (
            (7, "campaign:super-reduce:2026-09-01"),
            (8, "campaign:super88:49462:49469"),
        ):
            response = client.post(
                "/api/campaigns/refresh-evidence",
                headers={"X-API-Key": TOKEN},
                json={"workflow_key": workflow_key, "plan_id": plan_id},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["plan_id"] == plan_id
            assert body["workflow_key"] == workflow_key
            assert body["created"] is False and body["reused"] is True
            assert body["export_evidence"]["sha256"] == f"sha256-plan-{plan_id}"
            assert set(body["gate_results"]) == {"R16", "R17"}
            assert body["execution_boundary"] == {
                "erp_source": "formal_backend_services",
                "browser_reads_erp_pages": False,
                "platform_write": False,
                "account_action": False,
                "notification": False,
                "automatic_retry": False,
                "allowed_next_browser_scope": (
                    "external_platform_login_discovery_upload_submit_and_official_receipt_only"),
                "platform_read": "current_activity_export_only",
            }

        denied = client.post(
            "/api/campaigns/8/precheck",
            headers={"X-API-Key": TOKEN})
        assert denied.status_code == 401
        with Session() as db:
            audits = db.execute(select(AuditLog).where(
                AuditLog.path == "/api/campaigns/refresh-evidence"
            ).order_by(AuditLog.id)).scalars().all()
            assert [row.status_code for row in audits[-2:]] == [200, 200]
            assert all(
                row.username == "service:campaign-prepare" for row in audits[-2:])
            assert all(
                row.note == "authenticated path-scoped machine request"
                for row in audits[-2:])
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_prepare_service_identity_uses_route_validation_and_is_audited(monkeypatch):
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True)
    with Session() as db:
        settings_service.set_value(
            db, dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING, TOKEN)
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(middleware, "SessionLocal", Session)
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    try:
        client = TestClient(app)
        response = client.post(
            "/api/campaigns/prepare",
            headers={"X-API-Key": TOKEN},
            json=_payload(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["workflow_key"] == _payload()["workflow_key"]
        assert body["plan"]["remark"] == (
            "official_all_store=true; official_exempt_items=")
        r15 = next(
            check for check in body["preflight"]["checks"]
            if check["rule"] == "R15")
        assert r15["level"] != "error"
        assert body["execution_boundary"]["platform_write"] is False
        assert body["execution_boundary"]["account_action"] is False

        # A malformed payload still runs through the same Pydantic route gate.
        invalid = client.post(
            "/api/campaigns/prepare",
            headers={"X-API-Key": TOKEN},
            json={"workflow_key": "campaign:bad"},
        )
        assert invalid.status_code == 422

        with Session() as db:
            audits = db.execute(select(AuditLog).where(
                AuditLog.path == "/api/campaigns/prepare"
            ).order_by(AuditLog.id)).scalars().all()
            assert [row.status_code for row in audits[-2:]] == [200, 422]
            assert all(
                row.username == "service:campaign-prepare"
                for row in audits[-2:])
            assert all(
                row.note == "authenticated path-scoped machine request"
                for row in audits[-2:])
            assert all(TOKEN not in str(row.request_body) for row in audits[-2:])
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_wrong_prepare_key_cannot_reach_endpoint(monkeypatch):
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as db:
        settings_service.set_value(
            db, dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING, TOKEN)
        db.commit()

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(middleware, "SessionLocal", Session)
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    try:
        response = TestClient(app).post(
            "/api/campaigns/prepare",
            headers={"X-API-Key": "wrong"},
            json=_payload(),
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()

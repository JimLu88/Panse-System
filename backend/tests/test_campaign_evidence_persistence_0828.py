from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.models.campaign import CampaignPlan
from app.services import (
    campaign_price_floor_service,
    campaign_service,
    campaign_workflow_service,
    settings_service,
    web_agent_service,
)


def _plan(db, *, workflow_key="campaign:test:evidence"):
    plan = CampaignPlan(
        name="证据持久化回归",
        campaign_type="big88",
        tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="超级88现货",
        status="draft",
        workflow_key=workflow_key,
        remark="official_all_store=true; official_exempt_items=",
        platform_activity_mode="fixed_window",
        platform_campaign_id="49462",
        platform_united_activity_id="49469",
    )
    db.add(plan)
    db.commit()
    return plan


def test_explicit_empty_plan_scope_never_falls_back_to_legacy(db_session):
    plan = _plan(db_session)
    settings_service.set_value(
        db_session, campaign_price_floor_service.EVIDENCE_KEY,
        '{"old-plan-sku":{"sku_id":"old-plan-sku"}}')
    settings_service.set_value(
        db_session,
        f"{campaign_price_floor_service.PLAN_EVIDENCE_KEY_PREFIX}{plan.id}",
        "{}")
    db_session.commit()

    assert campaign_price_floor_service.evidence_map(
        db_session, plan=plan) == {}


def test_placeholder_prices_keep_only_current_candidate_skus(db_session):
    plan = _plan(db_session)

    result = campaign_service.record_placeholder_live_prices(
        db_session,
        plan,
        [
            {"sku_id": "6000000001", "list_price": 397},
            {"sku_id": "6000000002", "list_price": 500},
            {"sku_id": "6000000003", "list_price": 999},
        ],
        ["6000000001", "6000000002", "6000000004"],
    )
    db_session.commit()

    assert result["observed"] == 2
    assert result["missing_sku_ids"] == ["6000000004"]
    assert campaign_service.placeholder_live_prices_for_plan(
        db_session, plan) == {
            "6000000001": Decimal("397"),
            "6000000002": Decimal("500"),
        }


def test_successful_refresh_commits_even_when_package_stays_blocked(
        tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'refresh.db'}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True)
    with Session() as db:
        plan = _plan(db)
        plan_id = plan.id

    def fake_refresh(db, plan):
        settings_service.set_value(
            db, "pytest_refresh_commit_marker", str(plan.id))
        db.flush()
        return {
            "ok": True,
            "rows": [],
            "floor_refresh": {"observed": 1},
            "placeholder_price_refresh": {"observed": 1},
            "export_evidence": {"sha256": "fresh"},
        }

    def blocked_package(_db, plan, **_kwargs):
        return {
            "ok": True,
            "created": False,
            "reused": True,
            "workflow_key": plan.workflow_key,
            "plan": plan,
            "preflight": {"checks": [
                {"rule": "R16", "level": "error", "items": []},
                {"rule": "R17", "level": "error", "items": []},
            ]},
            "execution_boundary": {},
        }

    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        fake_refresh)
    monkeypatch.setattr(
        campaign_workflow_service, "_package_existing", blocked_package)

    with Session() as db:
        result = campaign_workflow_service.refresh_evidence_and_prepare(
            db, workflow_key="campaign:test:evidence",
            expected_plan_id=plan_id)
        assert result["gate_results"]["R16"]["level"] == "error"

    with Session() as db:
        assert settings_service.get(
            db, "pytest_refresh_commit_marker", env_fallback=False) == str(plan_id)
    engine.dispose()


def test_web_agent_failure_preserves_job_and_read_only_step(
        db_session, monkeypatch):
    monkeypatch.setattr(
        web_agent_service, "_post",
        lambda *_args, **_kwargs: {"ok": True, "job": "job-plan-8"})
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_args, **_kwargs: {
            "status": "done",
            "result": {
                "ok": False,
                "step": "export_click",
                "error": "export_button_not_found",
                "item_note": "item:quick_signup_closed",
                "page": {"title": "超级88现货"},
            },
        })

    result = web_agent_service.campaign_export_items(
        db_session, "超级88现货", campaign_id="49462",
        united_activity_id="49469")

    assert result["job_id"] == "job-plan-8"
    assert result["step"] == "export_click"
    assert result["error"] == "export_button_not_found"
    assert result["detail"]["page"]["title"] == "超级88现货"

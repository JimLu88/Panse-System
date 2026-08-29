from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.models.campaign import CampaignPlan
from app.services import (
    campaign_price_floor_service,
    campaign_recon_service,
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


def test_web_agent_sign_record_payload_keeps_exact_guard_identity(
        db_session, monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-sign-record"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_args, **_kwargs: {
            "status": "done",
            "result": {
                "ok": True,
                "xlsx_b64": "UEs=",
                "filename": "registered.xlsx",
            },
        })

    result = web_agent_service.campaign_export_items(
        db_session,
        "26年淘宝9月超级88",
        campaign_id="49462",
        united_activity_id="49469",
        sign_record_id="3527841611",
        campaign_phase="超级88现货",
        campaign_start="2026-09-06 20:00:00",
        campaign_end="2026-09-13 23:59:59",
        official_rate="12%",
    )

    assert result["ok"] is True
    assert captured["path"] == "/api/campaign/export-items"
    assert captured["payload"]["sign_record_id"] == "3527841611"
    assert captured["payload"]["campaign_id"] == "49462"
    assert captured["payload"]["united_activity_id"] == "49469"
    assert captured["payload"]["campaign_start"] == "2026-09-06 20:00:00"


def test_web_agent_candidate_evidence_keeps_exact_scope_and_readonly_result(
        db_session, monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-candidate-evidence"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_args, **_kwargs: {
            "status": "done",
            "result": {
                "ok": True,
                "records": [{
                    "item_id": "1047741358718",
                    "sku_id": "6291475451145",
                    "current_list_price": 8630,
                    "min_list_price": 6472.5,
                    "max_eligible_activity_price": 5137.41,
                }],
                "missing_sku_ids": [],
                "sha256": "candidate-sha",
                "execution_boundary": {
                    "platform_write": False,
                    "account_action": False,
                },
            },
        })
    scope = [{
        "item_id": "1047741358718",
        "sku_ids": ["6291475451145"],
    }]

    result = web_agent_service.campaign_candidate_price_evidence(
        db_session,
        "26年淘宝9月超级88",
        campaign_id="49462",
        united_activity_id="49469",
        sign_record_id="3527841611",
        campaign_phase="超级88现货",
        campaign_start="2026-09-06 20:00:00",
        campaign_end="2026-09-13 23:59:59",
        official_rate="12%",
        candidate_scope=scope,
    )

    assert result["ok"] is True
    assert result["job_id"] == "job-candidate-evidence"
    assert result["execution_boundary"]["platform_write"] is False
    assert captured["path"] == "/api/campaign/candidate-price-evidence"
    assert captured["payload"]["candidate_scope"] == scope
    assert captured["payload"]["sign_record_id"] == "3527841611"


def test_refresh_merges_enrolled_and_candidate_evidence_without_platform_write(
        db_session, monkeypatch):
    plan = _plan(db_session)
    plan.name = "超级88现货"
    plan.qn_campaign_title = "26年淘宝9月超级88"
    plan.platform_sign_record_id = "3527841611"
    db_session.commit()
    placeholder_rows = {}

    monkeypatch.setattr(
        campaign_service, "build_signup_rows",
        lambda *_args, **_kwargs: ([
            {"taobao_item_id": "1047741358718",
             "taobao_sku_id": "6291475451145", "is_placeholder": False},
            {"taobao_item_id": "1047741358718",
             "taobao_sku_id": "6241061986676", "is_placeholder": True},
        ], {"placeholder_candidate_sku_ids": ["6241061986676"]}))
    monkeypatch.setattr(
        web_agent_service, "campaign_export_items",
        lambda *_args, **_kwargs: {
            "ok": True, "xlsx_bytes": b"registered", "filename": "registered.xlsx",
            "job_id": "job-enrolled"})
    monkeypatch.setattr(
        campaign_recon_service, "parse_activity_items_export",
        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        campaign_recon_service, "parse_activity_floor_evidence_export",
        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        web_agent_service, "campaign_candidate_price_evidence",
        lambda *_args, **_kwargs: {
            "ok": True,
            "records": [
                {"item_id": "1047741358718", "sku_id": "6291475451145",
                 "current_list_price": 8630, "min_list_price": 6472.5,
                 "max_eligible_activity_price": 5137.41},
                {"item_id": "1047741358718", "sku_id": "6241061986676",
                 "current_list_price": 1000, "min_list_price": 1000,
                 "max_eligible_activity_price": 397},
            ],
            "missing_sku_ids": [], "requested_sku_count": 2,
            "observed_sku_count": 2, "candidate_items_scanned": 91,
            "page_count": 10, "sha256": "candidate-sha",
            "job_id": "job-candidate", "identity": {"sign_record_id": "3527841611"},
            "selection_guard": {"checked": 0, "zero_selected": True},
            "execution_boundary": {"platform_write": False,
                                     "account_action": False},
        })

    def fake_placeholder(_db, _plan, rows, candidate_skus):
        placeholder_rows["rows"] = list(rows)
        placeholder_rows["candidate_skus"] = list(candidate_skus)
        return {"observed": 1, "missing_sku_ids": []}

    monkeypatch.setattr(
        campaign_service, "record_placeholder_live_prices", fake_placeholder)

    result = campaign_service.refresh_floor_evidence_from_current_activity(
        db_session, plan)

    assert result["ok"] is True
    assert result["candidate_floor_refresh"]["complete"] == 2
    assert result["candidate_evidence"]["sha256"] == "candidate-sha"
    assert result["candidate_evidence"]["execution_boundary"][
        "platform_write"] is False
    assert placeholder_rows["candidate_skus"] == ["6241061986676"]
    candidate_prices = {
        row["sku_id"]: row["list_price"]
        for row in placeholder_rows["rows"] if row.get("list_price")
    }
    assert candidate_prices["6241061986676"] == 1000

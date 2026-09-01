"""Plan-8 whole-item omission and one-shot execution regression gates."""
from datetime import datetime

from sqlalchemy import select

from app import dependencies
from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import campaign_plan8_execute_service, campaign_service


def _plan() -> CampaignPlan:
    return CampaignPlan(
        id=8,
        workflow_key=campaign_plan8_execute_service.WORKFLOW_KEY,
        name="超级88现货",
        campaign_type="big88",
        tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="26年淘宝9月超级88",
        status="alarmed",
        remark="official_all_store=true; official_exempt_items=805268708396",
        platform_activity_mode="fixed_window",
        platform_campaign_id="49462",
        platform_united_activity_id="49469",
        platform_sign_record_id="3527841611",
    )


def _request() -> dict:
    return {
        "workflow_key": campaign_plan8_execute_service.WORKFLOW_KEY,
        "expected_plan_id": 8,
        "expected_status": "alarmed",
        "expected_candidate_sha256": (
            campaign_plan8_execute_service.EXPECTED_CANDIDATE_SHA256),
    }


def test_plan8_route_is_in_the_exact_service_identity_allowlist():
    assert dependencies.CAMPAIGN_PLAN8_EXECUTE_PATH == (
        "/api/campaigns/execute-super88-plan8")
    assert (dependencies.CAMPAIGN_PLAN8_EXECUTE_PATH
            in dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)


def _patch_scope(monkeypatch):
    monkeypatch.setattr(
        campaign_service, "candidate_unavailable_items_for_plan",
        lambda *_a, **_k: {
            "793202812082": {
                "evidence_sha256": (
                    campaign_plan8_execute_service.EXPECTED_CANDIDATE_SHA256),
            },
        })
    monkeypatch.setattr(
        campaign_service, "preflight", lambda *_a, **_k: [
            {"rule": "R16", "level": "pass"},
            {"rule": "R17", "level": "pass", "checked": 2},
        ])
    monkeypatch.setattr(
        campaign_service, "build_signup_rows", lambda *_a, **_k: ([{
            "taobao_item_id": "1044450741007",
            "taobao_sku_id": "6100000000001",
            "price": 1000,
            "is_placeholder": False,
        }], {"excluded_whole_items": []}))
    monkeypatch.setattr(
        campaign_service, "build_discount_rows", lambda *_a, **_k: ([{
            "taobao_item_id": "1044450741007",
            "taobao_sku_id": "6100000000001",
            "deduct": 200,
            "target_price": 680,
        }], {"excluded_whole_items": []}))


def test_plan8_executes_once_and_replay_never_writes_again(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    _patch_scope(monkeypatch)
    calls = []

    def fake_discount(db, current, phase):
        calls.append(("discount", phase))
        current.status = "discount_pushed"
        db.commit()
        return {"ok": True, "submitted": True, "job": "discount-job"}

    def fake_signup(db, current, execution_source):
        calls.append(("signup", execution_source))
        current.status = "signup_pushed"
        db.commit()
        return {"ok": True, "submitted": True, "job": "signup-job"}

    monkeypatch.setattr(campaign_service, "push_discount", fake_discount)
    monkeypatch.setattr(campaign_service, "push_signup", fake_signup)

    first = campaign_plan8_execute_service.execute_plan8(
        db_session, **_request())
    replay = campaign_plan8_execute_service.execute_plan8(
        db_session, **_request())

    assert first["ok"] is True
    assert first["plan_status"] == "signup_pushed"
    assert replay["ok"] is True and replay["idempotent_replay"] is True
    assert calls == [
        ("discount", "commit"),
        ("signup", "campaign_automation"),
    ]
    attempt = db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == (
            campaign_plan8_execute_service.OPERATION),
    )).scalar_one()
    assert attempt.state == "completed"
    assert attempt.write_claimed is True
    assert attempt.automatic_retry_allowed is False


def test_plan8_rejects_candidate_scope_change_before_any_write(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    monkeypatch.setattr(
        campaign_service, "candidate_unavailable_items_for_plan",
        lambda *_a, **_k: {})
    monkeypatch.setattr(
        campaign_service, "push_discount",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must stop before platform write")))

    result = campaign_plan8_execute_service.execute_plan8(
        db_session, **_request())

    assert result["ok"] is False
    assert result["error"] == "plan8_candidate_unavailable_scope_mismatch"
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == (
            campaign_plan8_execute_service.OPERATION),
    )).scalar_one_or_none() is None

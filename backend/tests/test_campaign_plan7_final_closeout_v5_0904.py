from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import dependencies
from app.cli import campaign_execute_plan7_final_closeout_v5 as cli
from app.models.campaign import (
    CampaignExecutionAttempt,
    CampaignPlan,
    CampaignPreparationBundle,
)
from app.services import campaign_plan7_final_closeout_v5_service as service


def _seed_residue(db_session):
    now = datetime.now(timezone.utc)
    plan = CampaignPlan(
        id=service.PLAN_ID, workflow_key=service.WORKFLOW_KEY,
        name="p7", campaign_type="super_reduce",
        platform_activity_mode="long_running_update",
        qn_campaign_title="超级立减", status=service.EXPECTED_PLAN_STATUS,
        start_at=now, end_at=now + timedelta(days=1),
    )
    official_identity = {
        "ok": True, "checked_items": 1,
        "checked_skus": service.v4.EXPECTED_SIGNUP_ROWS,
        "official_skus": service.v4.EXPECTED_SIGNUP_ROWS,
        "artifact": {"sha256": service.v4.OFFICIAL_EXPORT_SHA256},
    }
    failed_result = {
        "bundle_id": service.PREPARED_BUNDLE_ID,
        "attempt_id": service.PREPARED_ATTEMPT_ID,
        "result": {
            "step": "plan7_final_closeout_v4_policy_guard",
            "detail": {
                "error": "bundle_already_consumed",
                "bundle_id": service.PREPARED_BUNDLE_ID,
            },
        },
    }
    failed_v4 = CampaignExecutionAttempt(
        id=service.FAILED_V4_INVOCATION_ID, plan_id=service.PLAN_ID,
        workflow_key=service.WORKFLOW_KEY,
        operation=service.v4.INVOCATION_OPERATION,
        scope_sha256="1" * 64, state="failed_no_retry",
        write_claimed=False, platform_write_observed=None,
        automatic_retry_allowed=False,
        error_code="计划7最终收口 V4 上下文不完整，拒绝进入平台写入",
        result_summary=failed_result,
    )
    attempt = CampaignExecutionAttempt(
        id=service.PREPARED_ATTEMPT_ID, plan_id=service.PLAN_ID,
        workflow_key=service.WORKFLOW_KEY, operation="signup",
        scope_sha256="2" * 64, state="prepared", write_claimed=False,
        platform_write_observed=None, automatic_retry_allowed=False,
        result_summary={
            "prepared_bundle_id": service.PREPARED_BUNDLE_ID,
            "source_bundle_id": service.v4.SOURCE_BUNDLE_ID,
            "official_export_sha256": service.v4.OFFICIAL_EXPORT_SHA256,
            "signup_rows": service.v4.EXPECTED_SIGNUP_ROWS,
            "discount_rows_verified": service.v4.EXPECTED_DISCOUNT_ROWS,
            "invocation_id": service.FAILED_V4_INVOCATION_ID,
            "official_product_sku_identity": official_identity,
        },
    )
    bundle = CampaignPreparationBundle(
        id=service.PREPARED_BUNDLE_ID, plan_id=service.PLAN_ID,
        workflow_key=service.WORKFLOW_KEY, revision=5,
        state="ready_for_final_submission", prepared_by="test",
        source_sha256=service.PREPARED_BUNDLE_SOURCE_SHA256,
        policy_sha256=service.v4.POLICY_SHA256,
        manifest_sha256=service.PREPARED_BUNDLE_MANIFEST_SHA256,
        identity={}, summary={},
        signup_rows=[{"row": index} for index in range(
            service.v4.EXPECTED_SIGNUP_ROWS)],
        discount_rows=[{"row": index} for index in range(
            service.v4.EXPECTED_DISCOUNT_ROWS)],
        item_decisions=[], gate_results=[], evidence_snapshot_ids=[],
        execution_boundary={"platform_write": False},
        prepared_at=now, expires_at=now + timedelta(hours=2),
        consumed_at=now, consumed_attempt_id=service.PREPARED_ATTEMPT_ID,
    )
    db_session.add_all([plan, failed_v4, attempt, bundle])
    db_session.commit()
    return plan, failed_v4, attempt, bundle


def test_v5_fixed_request_cli_and_auth_scope():
    assert service.validate_request(service.request_payload()) is True
    changed = service.request_payload()
    changed["prepared_bundle_id"] = "0" * 24
    assert service.validate_request(changed) is False
    assert cli._FIXED_PAYLOAD == service.request_payload()
    assert cli._URL.endswith(
        "/execute-super-reduce-plan7-final-closeout-v5")
    assert dependencies.CAMPAIGN_PLAN7_FINAL_CLOSEOUT_V5_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)


def test_v5_accepts_only_exact_prewrite_residue(db_session):
    plan, failed_v4, attempt, bundle = _seed_residue(db_session)
    assert service._residue_error(
        plan, failed_v4, attempt, bundle) is None
    attempt.write_claimed = True
    assert service._residue_error(
        plan, failed_v4, attempt, bundle
    ) == "final_closeout_v5_prepared_attempt_residue_mismatch"


def test_v5_releases_bundle_only_inside_guarded_push_and_reconsumes(
        db_session, monkeypatch):
    _seed_residue(db_session)
    monkeypatch.setattr(
        service.campaign_policy_service, "require_policy",
        lambda: {"_sha256": service.v4.POLICY_SHA256})

    def _validate(db, plan, **kwargs):
        bundle = db.get(CampaignPreparationBundle, service.PREPARED_BUNDLE_ID)
        assert bundle.consumed_attempt_id is None
        assert kwargs["prepared_bundle_context"]["bundle_id"] == bundle.id
        return True, {"error": None, "bundle_id": bundle.id}

    monkeypatch.setattr(service.v4, "validate_push_context", _validate)

    def _push(db, plan, **kwargs):
        bundle = db.get(CampaignPreparationBundle, service.PREPARED_BUNDLE_ID)
        assert bundle.consumed_attempt_id is None
        assert kwargs["execution_source"] == service.v4.EXECUTION_SOURCE
        assert kwargs["exact_item_scope"] == {service.v4.TARGET_ITEM_ID}
        attempt = db.get(CampaignExecutionAttempt, service.PREPARED_ATTEMPT_ID)
        attempt.state = "completed"
        attempt.write_claimed = True
        attempt.platform_write_observed = True
        db.commit()
        return {"ok": True, "submitted": True}

    monkeypatch.setattr(service.campaign_service, "push_signup", _push)
    result = service.execute_plan7_final_closeout_v5(
        db_session, service.request_payload())

    assert result["ok"] is True, result
    assert result["attempt_id"] == service.PREPARED_ATTEMPT_ID
    bundle = db_session.get(
        CampaignPreparationBundle, service.PREPARED_BUNDLE_ID)
    assert bundle.consumed_attempt_id == service.PREPARED_ATTEMPT_ID
    assert bundle.consumed_at is not None
    assert db_session.get(CampaignPlan, service.PLAN_ID).status == "reconciled"

    replay = service.execute_plan7_final_closeout_v5(
        db_session, service.request_payload())
    assert replay["error"] == "final_closeout_v5_plan_residue_mismatch"


def test_v4_source_no_longer_preconsumes_before_push():
    source = open(service.v4.__file__, encoding="utf-8").read()
    call = "result = campaign_service.push_signup("
    assert source.index(call) < source.index(
        "consumed_bundle.consumed_at = datetime.now(timezone.utc)")

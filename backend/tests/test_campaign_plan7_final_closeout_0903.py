from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.campaign import (
    CampaignExecutionAttempt,
    CampaignPlan,
    CampaignPreparationBundle,
)
from app.services import campaign_plan7_final_closeout_service as service
from app.cli import campaign_execute_plan7_final_closeout_v3 as cli_v3


def _identity():
    return {
        "ok": True,
        "campaign_title": "超级立减",
        "campaign_id": None,
        "united_activity_id": None,
        "sign_record_id": None,
        "campaign_start": "2026-09-01 00:00:00",
        "campaign_end": "2026-09-05 23:59:59",
        "platform_activity_mode": "long_running_update",
        "official_rate": "10%",
    }


def _rows():
    signup = [{
        "taobao_item_id": service.TARGET_ITEM_ID,
        "taobao_sku_id": str(6000000000000 + i),
        "sku_code": f"SKU-{i}",
        "price": 1000 + i,
        "is_placeholder": i >= 9,
    } for i in range(service.EXPECTED_SIGNUP_ROWS)]
    discount = [{
        "taobao_item_id": service.TARGET_ITEM_ID,
        "taobao_sku_id": signup[i]["taobao_sku_id"],
        "sku_code": signup[i]["sku_code"],
        "deduct": 100,
        "official": 100,
        "target_price": 800 + i,
        "calculation_base": 1000 + i,
    } for i in range(service.EXPECTED_DISCOUNT_ROWS)]
    return signup, discount


def _install_bundle(db_session):
    signup, discount = _rows()
    plan = CampaignPlan(
        id=service.PLAN_ID,
        workflow_key=service.WORKFLOW_KEY,
        name="plan7 closeout",
        campaign_type="super_reduce",
        platform_activity_mode="long_running_update",
        qn_campaign_title="超级立减",
        status=service.EXPECTED_STATUS,
        start_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 5, 23, 59, 59, tzinfo=timezone.utc),
        price_protection_days=19,
        remark="official_all_store=true; official_exempt_items=805268708396",
    )
    bundle = CampaignPreparationBundle(
        id=service.BUNDLE_ID,
        plan_id=service.PLAN_ID,
        workflow_key=service.WORKFLOW_KEY,
        revision=4,
        state="ready_for_final_submission",
        prepared_by="service:campaign-preparation-bundle",
        source_sha256=service.SOURCE_SHA256,
        policy_sha256=service.POLICY_SHA256,
        manifest_sha256=service.MANIFEST_SHA256,
        identity=_identity(),
        summary={
            "compiler_schema_version": "2026-09-03.3",
            "exact_item_scope": sorted({
                service.TARGET_ITEM_ID, *service.DEFERRED_ITEM_IDS}),
            "exact_item_scope_sha256": service.ITEM_SCOPE_SHA256,
            "global_blockers": [],
        },
        signup_rows=signup,
        discount_rows=discount,
        item_decisions=[
            {"taobao_item_id": service.TARGET_ITEM_ID, "state": "ready"},
            *[{"taobao_item_id": item_id, "state": "deferred_whole_item"}
              for item_id in sorted(service.DEFERRED_ITEM_IDS)],
        ],
        gate_results=[],
        evidence_snapshot_ids=[],
        execution_boundary={"platform_write": False},
        prepared_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db_session.add_all([plan, bundle])
    db_session.commit()
    return plan, bundle, signup, discount


def _request(**overrides):
    values = {
        "workflow_key": service.WORKFLOW_KEY,
        "expected_plan_id": service.PLAN_ID,
        "expected_status": service.EXPECTED_STATUS,
        "bundle_id": service.BUNDLE_ID,
        "expected_source_sha256": service.SOURCE_SHA256,
        "expected_policy_sha256": service.POLICY_SHA256,
        "expected_manifest_sha256": service.MANIFEST_SHA256,
        "expected_item_scope_sha256": service.ITEM_SCOPE_SHA256,
        "recovery_id": service.RECOVERY_ID,
        "expected_web_agent_commit": service.EXPECTED_WEB_AGENT_COMMIT,
    }
    values.update(overrides)
    return values


def _install_common(monkeypatch, signup, discount):
    monkeypatch.setattr(
        service.campaign_policy_service, "require_policy",
        lambda: {"_sha256": service.POLICY_SHA256})
    monkeypatch.setattr(
        service.campaign_service, "build_signup_rows",
        lambda db, plan: (list(signup), {}))
    monkeypatch.setattr(
        service.campaign_service, "build_discount_rows",
        lambda db, plan: (list(discount), {}))
    monkeypatch.setattr(
        service.campaign_service, "campaign_identity", lambda plan: _identity())
    monkeypatch.setattr(
        service.campaign_service, "preflight",
        lambda db, plan, exact_item_scope=None: [
            {"rule": rule, "level": "pass"}
            for rule in ("R7", "R13", "R16", "R17")])


def test_rejects_any_changed_bundle_identity_without_platform_read(
        db_session, monkeypatch):
    _plan, _bundle, signup, discount = _install_bundle(db_session)
    _install_common(monkeypatch, signup, discount)
    called = []
    monkeypatch.setattr(
        service.campaign_service, "_refresh_official_product_sku_identity",
        lambda *args, **kwargs: called.append(True))

    result = service.execute_plan7_final_closeout(
        db_session, **_request(expected_manifest_sha256="0" * 64))

    assert result["error"] == "final_closeout_request_not_allowed"
    assert called == []
    assert db_session.query(CampaignExecutionAttempt).count() == 0


def test_identity_failure_does_not_consume_bundle_or_create_claim(
        db_session, monkeypatch):
    _plan, bundle, signup, discount = _install_bundle(db_session)
    _install_common(monkeypatch, signup, discount)
    monkeypatch.setattr(
        service, "_manifest_sha",
        lambda identity, policy_sha, signup_rows, discount_rows:
        service.MANIFEST_SHA256)
    monkeypatch.setattr(
        service.campaign_service, "_refresh_official_product_sku_identity",
        lambda *args, **kwargs: {
            "ok": False, "error": "official_product_sku_scope_mismatch"})

    result = service.execute_plan7_final_closeout(db_session, **_request())

    assert result["error"] == "final_closeout_official_sku_identity_failed"
    assert result["execution_boundary"]["platform_read"] is True
    assert db_session.query(CampaignExecutionAttempt).count() == 0
    db_session.refresh(bundle)
    assert bundle.consumed_attempt_id is None


def test_success_consumes_once_and_preserves_all_non_target_items(
        db_session, monkeypatch):
    plan, bundle, signup, discount = _install_bundle(db_session)
    _install_common(monkeypatch, signup, discount)
    monkeypatch.setattr(
        service, "_manifest_sha",
        lambda identity, policy_sha, signup_rows, discount_rows:
        service.MANIFEST_SHA256)
    identity_result = {
        "ok": True, "checked_items": 1,
        "checked_skus": service.EXPECTED_SIGNUP_ROWS,
        "artifact": {"sha256": "a" * 64},
    }
    monkeypatch.setattr(
        service.campaign_service, "_refresh_official_product_sku_identity",
        lambda *args, **kwargs: identity_result)
    captured = {}

    def fake_push(db, current, **kwargs):
        captured.update(kwargs)
        attempt = db.query(CampaignExecutionAttempt).one()
        attempt.state = "completed"
        attempt.write_claimed = True
        attempt.platform_write_observed = True
        db.commit()
        current.status = "signup_pushed"
        db.commit()
        return {
            "ok": True, "submitted": True,
            "terminal_classification": {
                "accepted_item_ids": [service.TARGET_ITEM_ID],
                "hard_failed_item_ids": [], "no_sales_item_ids": []},
            "post_submit_verification": {"ok": True},
        }

    monkeypatch.setattr(service.campaign_service, "push_signup", fake_push)

    result = service.execute_plan7_final_closeout(db_session, **_request())

    assert result["ok"] is True
    assert result["plan_status"] == "reconciled"
    assert result["submitted_item_ids"] == [service.TARGET_ITEM_ID]
    assert set(result["deferred_item_ids"]) == service.DEFERRED_ITEM_IDS
    assert set(result["preserved_active_item_ids"]) == (
        service.PRESERVED_ACTIVE_ITEM_IDS)
    assert captured["execution_source"] == service.EXECUTION_SOURCE
    assert captured["exact_item_scope"] == {service.TARGET_ITEM_ID}
    assert captured["allow_terminal_no_sales_fallback"] is False
    assert captured["prepared_official_product_identity"] == identity_result
    assert captured["prepared_bundle_context"]["bundle_id"] == service.BUNDLE_ID
    assert result["recovery_id"] == service.RECOVERY_ID
    assert result["expected_web_agent_commit"] == service.EXPECTED_WEB_AGENT_COMMIT
    db_session.refresh(bundle)
    assert bundle.consumed_attempt_id == result["attempt_id"]
    db_session.refresh(plan)
    assert plan.status == "reconciled"


def test_consumed_nonterminal_bundle_is_never_retried(db_session, monkeypatch):
    plan, bundle, signup, discount = _install_bundle(db_session)
    _install_common(monkeypatch, signup, discount)
    attempt = CampaignExecutionAttempt(
        id="claimed-once", plan_id=plan.id, workflow_key=plan.workflow_key,
        operation="signup", scope_sha256="f" * 64,
        state="unknown_no_retry", write_claimed=True,
        automatic_retry_allowed=False,
    )
    bundle.consumed_attempt_id = attempt.id
    bundle.consumed_at = datetime.now(timezone.utc)
    db_session.add(attempt)
    db_session.commit()

    result = service.execute_plan7_final_closeout(db_session, **_request())

    assert result["error"] == "final_closeout_already_claimed_no_retry"
    assert result["attempt_state"] == "unknown_no_retry"
    assert db_session.query(CampaignExecutionAttempt).count() == 1


def test_internal_push_guard_rejects_any_changed_bundle_context(
        db_session, monkeypatch):
    plan, _bundle, _signup, _discount = _install_bundle(db_session)
    plan.status = "resume_executing"
    db_session.commit()
    monkeypatch.setattr(
        service.campaign_policy_service, "require_policy",
        lambda: {"_sha256": service.POLICY_SHA256})

    result = service.campaign_service.push_signup(
        db_session, plan,
        execution_source=service.EXECUTION_SOURCE,
        reuse_fresh_plan_evidence=True,
        exact_item_scope={service.TARGET_ITEM_ID},
        allow_terminal_no_sales_fallback=False,
        prepared_official_product_identity={
            "ok": True, "checked_items": 1,
            "checked_skus": service.EXPECTED_SIGNUP_ROWS},
        prepared_bundle_context={"bundle_id": "changed"},
    )

    assert result["ok"] is False
    assert result["step"] == "plan7_final_closeout_policy_guard"
    assert result["automatic_retry"] is False


def test_v3_cli_is_bound_to_repair_and_exact_bundle_identity():
    assert cli_v3._URL.endswith(
        "/execute-super-reduce-plan7-final-closeout-v3")
    assert cli_v3._FIXED_PAYLOAD == {
        "workflow_key": service.WORKFLOW_KEY,
        "plan_id": service.PLAN_ID,
        "expected_status": service.EXPECTED_STATUS,
        "bundle_id": service.BUNDLE_ID,
        "expected_source_sha256": service.SOURCE_SHA256,
        "expected_policy_sha256": service.POLICY_SHA256,
        "expected_manifest_sha256": service.MANIFEST_SHA256,
        "expected_item_scope_sha256": service.ITEM_SCOPE_SHA256,
        "recovery_id": service.RECOVERY_ID,
        "expected_web_agent_commit": service.EXPECTED_WEB_AGENT_COMMIT,
    }

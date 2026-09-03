import json
from io import BytesIO

from app import dependencies
from app.api import campaigns
from app.cli import campaign_recover_plan8_final_v7 as cli
from app.models.campaign import CampaignExecutionAttempt
from app.services import (
    campaign_plan8_final_recovery_v7_service as recovery,
    web_agent_service,
)
from backend.tests.test_campaign_plan8_final_recovery_v6_0903 import (
    _discount_rows,
    _patch_scope,
    _plan,
    _seed_prerequisites,
    _signup_rows,
    _web_result,
)


def _seed_v6_terminal(db):
    db.add(CampaignExecutionAttempt(
        id=recovery.V6_ATTEMPT_ID, plan_id=8,
        workflow_key=recovery.WORKFLOW_KEY,
        operation="plan8_final_recovery_v6", scope_sha256="9" * 64,
        state="unknown_no_retry", write_claimed=True,
        platform_write_observed=True, automatic_retry_allowed=False,
    ))
    db.commit()


def test_v7_manifest_binds_v6_terminal_and_new_activity_creation():
    manifest = recovery._fixed_manifest(
        _signup_rows(), _discount_rows(), recovery.EXPECTED_POLICY_SHA256)
    assert manifest["recovery_version"] == 7
    assert manifest["recovery_evidence"] == recovery.RECOVERY_EVIDENCE
    assert manifest["execution_order"][0] == (
        "create_new_8_sku_single_item_discount_activity")


def test_v7_route_and_machine_identity_are_narrowly_allowlisted(monkeypatch):
    assert recovery.EXECUTION_SOURCE == "campaign_super88_plan8_final_recovery_v7"
    assert (dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V7_PATH
            in dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert (dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V7_CLAIM_VERIFY_PATH
            not in dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    monkeypatch.setattr(
        dependencies.settings_service, "get",
        lambda _db, key, **_kwargs: (
            "secret" if key == "web_agent_token" else None))
    assert dependencies.machine_identity_for_key(
        "secret", object(),
        path=dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V7_CLAIM_VERIFY_PATH
    ) == "machine:web-agent-plan8-v7-claim-verify"
    paths = {route.path for route in campaigns.router.routes}
    assert "/api/campaigns/recover-super88-plan8-final-v7" in paths
    assert ("/api/campaigns/recover-super88-plan8-final-v7/claim-verification"
            in paths)


def test_v7_readback_accepts_one_new_activity_and_rejects_old_activity():
    manifest = recovery._fixed_manifest(
        _signup_rows(), _discount_rows(), recovery.EXPECTED_POLICY_SHA256)
    inspect = _web_result({"manifest": manifest,
                           "scope_sha256": recovery.v6._hash(manifest)},
                          phase="inspect")
    ok, detail = recovery.v6.validate_inspection(
        inspect, manifest, recovery.v6._hash(manifest))
    assert ok, detail
    enriched = recovery.v6.enrich_manifest_with_inspection(
        manifest, detail,
        inspect_scope_sha256=recovery.v6._hash(manifest))
    scope = recovery.v6._hash(enriched)
    result = _web_result({"manifest": enriched, "scope_sha256": scope},
                         phase="readback")
    for row in result["discount_rows"]:
        row["activity_id"] = "144300000001"
    ok, detail = recovery.validate_readback(result, enriched, scope)
    assert ok, detail
    assert detail["new_discount_activity_id"] == "144300000001"
    for row in result["discount_rows"]:
        row["activity_id"] = recovery.OLD_DISCOUNT_ACTIVITY_ID
    assert recovery.validate_readback(result, enriched, scope)[0] is False


def test_v7_full_flow_claims_once_and_completes(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _seed_v6_terminal(db_session)
    _patch_scope(db_session, monkeypatch)
    phases = []

    def fake_web(_db, *, payload, timeout_s=2400):
        phase = payload["phase"]
        phases.append(phase)
        result = _web_result(payload, phase=phase)
        if phase == "inspect":
            result["recovery_evidence"] = {
                "ok": True, "v6_attempt_id": recovery.V6_ATTEMPT_ID,
                "error_artifact_sha256": recovery.RECOVERY_EVIDENCE[
                    "v6_error_artifact_sha256"],
                "fresh_product_export_sha256": recovery.RECOVERY_EVIDENCE[
                    "fresh_product_export_sha256"],
            }
        elif phase == "commit":
            result["new_discount_activity_id"] = "144300000001"
        else:
            for row in result["discount_rows"]:
                row["activity_id"] = "144300000001"
        return result

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v7", fake_web)
    result = recovery.recover_plan8_final_v7(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=7, mode="execute",
        confirmation=recovery.EXECUTE_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert result["ok"] is True
    assert phases == ["inspect", "commit", "readback"]
    attempts = recovery._attempts(db_session)
    assert len(attempts) == 1
    assert attempts[0].state == "completed"


def test_v7_prerequisite_requires_exact_v6_unknown_no_retry(
        db_session):
    _seed_prerequisites(db_session)
    ok, detail = recovery._validate_prerequisites(db_session)
    assert ok is False
    assert detail[-1]["attempt_id"] == recovery.V6_ATTEMPT_ID
    _seed_v6_terminal(db_session)
    assert recovery._validate_prerequisites(db_session)[0] is True


def test_v7_cli_accepts_only_explicit_execute_or_readback(monkeypatch):
    payload = {
        "workflow_key": recovery.WORKFLOW_KEY, "plan_id": 8,
        "expected_status": "alarmed", "recovery_version": 7,
        "mode": "execute", "confirmation": recovery.EXECUTE_CONFIRMATION,
        "target_scope_sha256": recovery.EXPECTED_TARGET_SCOPE_SHA256,
    }
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["recovery_version"] == 7
    payload["confirmation"] = recovery.READBACK_CONFIRMATION
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    try:
        cli._read_payload()
    except ValueError:
        pass
    else:
        raise AssertionError("V7 accepted the wrong execution confirmation")

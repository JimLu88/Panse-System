import json
from io import BytesIO

from app import dependencies
from app.api import campaigns
from app.cli import campaign_recover_plan8_final_v8 as cli
from app.models.campaign import CampaignExecutionAttempt
from app.services import campaign_plan8_final_recovery_v8_service as recovery
from app.services import campaign_policy_service, web_agent_service
from backend.tests.test_campaign_plan8_final_recovery_v6_0903 import (
    _discount_rows,
    _patch_scope,
    _plan,
    _seed_prerequisites,
    _signup_rows,
    _web_result,
)


def test_web_agent_v8_start_wait_covers_cold_lazy_import(monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-v8", "lease_expires_at_epoch": 123}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda _db, _job_id, timeout_s: {
            "result": {"ok": True, "need_scan": False}
        },
    )

    result = web_agent_service.recover_plan8_final_v8(
        object(), payload={"phase": "inspect"}, timeout_s=2400)

    assert result["ok"] is True
    assert captured == {
        "path": "/api/campaign/plan8-final-recovery-v8",
        "payload": {"phase": "inspect"},
        "timeout": 420,
    }


def test_web_agent_v8_preserves_terminal_job_error(monkeypatch):
    monkeypatch.setattr(
        web_agent_service, "_post",
        lambda *_a, **_k: {"ok": True, "job": "job2"})
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_a, **_k: {
            "status": "error", "result": None,
            "error": "RuntimeError: browser_failed_before_claim",
            "error_code": "browser_failed_before_claim",
        })
    result = web_agent_service.recover_plan8_final_v8(
        object(), payload={"phase": "commit"})
    assert result == {
        "ok": False,
        "error": "RuntimeError: browser_failed_before_claim",
        "error_code": "browser_failed_before_claim",
        "status": "error",
        "last_checkpoint": None,
        "platform_write": None,
        "web_agent_job_id": "job2",
    }


def _seed_v7_zero_write(db):
    manifest = {"recovery_version": 7, "evidence": "zero-write"}
    scope = recovery.v6._hash(manifest)
    db.add(CampaignExecutionAttempt(
        id=recovery.V7_ATTEMPT_ID, plan_id=8,
        workflow_key=recovery.WORKFLOW_KEY,
        operation="plan8_final_recovery_v7", scope_sha256=scope,
        state="unknown_no_retry", write_claimed=True,
        platform_write_observed=None, automatic_retry_allowed=False,
        last_step="readback_not_complete", result_summary={"manifest": manifest},
    ))
    db.commit()


def test_v8_manifest_binds_v7_zero_write_and_publish_before_discount():
    manifest = recovery._fixed_manifest(
        _signup_rows(), _discount_rows(), recovery.EXPECTED_POLICY_SHA256)
    assert manifest["recovery_version"] == 8
    assert manifest["resume_evidence"] == recovery.EXPECTED_RESUME_EVIDENCE
    assert manifest["execution_order"] == recovery.EXECUTION_ORDER
    assert "recovery_evidence" not in manifest


def test_v8_route_and_machine_identity_are_narrowly_allowlisted(monkeypatch):
    assert recovery.EXECUTION_SOURCE == "campaign_super88_plan8_final_recovery_v8"
    assert (dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V8_PATH
            in dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert (dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V8_CLAIM_VERIFY_PATH
            not in dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    monkeypatch.setattr(
        dependencies.settings_service, "get",
        lambda _db, key, **_kwargs: (
            "secret" if key == "web_agent_token" else None))
    assert dependencies.machine_identity_for_key(
        "secret", object(),
        path=dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V8_CLAIM_VERIFY_PATH
    ) == "machine:web-agent-plan8-v8-claim-verify"
    paths = {route.path for route in campaigns.router.routes}
    assert "/api/campaigns/recover-super88-plan8-final-v8" in paths
    assert ("/api/campaigns/recover-super88-plan8-final-v8/claim-verification"
            in paths)
    assert (dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V8_PREUPLOAD_CLAIM_VERIFY_PATH
            not in dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert dependencies.machine_identity_for_key(
        "secret", object(),
        path=(dependencies
              .CAMPAIGN_PLAN8_FINAL_RECOVERY_V8_PREUPLOAD_CLAIM_VERIFY_PATH)
    ) == "machine:web-agent-plan8-v8-preupload-claim-verify"
    assert ("/api/campaigns/recover-super88-plan8-final-v8/"
            "preupload-claim-verification" in paths)


def test_v8_prerequisite_requires_exact_zero_write_v7(db_session):
    assert recovery._validate_prerequisite(db_session)[0] is False
    _seed_v7_zero_write(db_session)
    ok, detail = recovery._validate_prerequisite(db_session)
    assert ok is True
    assert detail["last_step"] == "readback_not_complete"
    row = db_session.get(CampaignExecutionAttempt, recovery.V7_ATTEMPT_ID)
    row.platform_write_observed = True
    db_session.commit()
    assert recovery._validate_prerequisite(db_session)[0] is False


def test_v8_full_flow_claims_once_and_completes(db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _seed_v7_zero_write(db_session)
    _patch_scope(db_session, monkeypatch)
    monkeypatch.setattr(
        campaign_policy_service, "require_policy",
        lambda: {"_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        recovery.v7, "_target_rows",
        lambda *_a, **_k: (_signup_rows(), None))
    monkeypatch.setattr(
        recovery.v7, "_discount_scope",
        lambda *_a, **_k: (_discount_rows(), None))
    phases = []

    def fake_web(_db, *, payload, timeout_s=2400):
        phase = payload["phase"]
        phases.append(phase)
        result = _web_result(payload, phase=phase)
        if phase == "inspect":
            result["resume_evidence"] = {
                "ok": True, **recovery.EXPECTED_RESUME_EVIDENCE}
            result["recovery_evidence"] = {
                "ok": True,
                "v6_attempt_id": recovery.v7.V6_ATTEMPT_ID,
                "error_artifact_sha256": recovery.v7.RECOVERY_EVIDENCE[
                    "v6_error_artifact_sha256"],
                "fresh_product_export_sha256": recovery.v7.RECOVERY_EVIDENCE[
                    "fresh_product_export_sha256"],
            }
        elif phase == "commit":
            result["checkpoints"] = recovery.EXPECTED_COMMIT_CHECKPOINTS
        return result

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v8", fake_web)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8, mode="execute",
        confirmation=recovery.EXECUTE_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert result["ok"] is True
    assert phases == ["inspect", "commit", "readback"]
    attempts = recovery._attempts(db_session)
    assert len(attempts) == 1
    assert attempts[0].state == "completed"


def _seed_v8_preclaim_failure(db, monkeypatch):
    old_manifest = {"recovery_version": 8, "preclaim": "failed-before-agent-claim"}
    old_scope = recovery.v6._hash(old_manifest)
    monkeypatch.setattr(recovery, "PRECLAIM_SCOPE_SHA256", old_scope)
    db.add(CampaignExecutionAttempt(
        id=recovery.PRECLAIM_ATTEMPT_ID, plan_id=8,
        workflow_key=recovery.WORKFLOW_KEY, operation=recovery.OPERATION,
        scope_sha256=old_scope, state="unknown_no_retry", write_claimed=True,
        platform_write_observed=None, automatic_retry_allowed=False,
        request_id=recovery.PRECLAIM_REQUEST_ID,
        last_step=recovery.PRECLAIM_LAST_STEP,
        error_code=recovery.PRECLAIM_ERROR_CODE,
        web_agent_job_id=recovery.PRECLAIM_WEB_AGENT_JOB_ID,
        result_summary={"manifest": old_manifest, "last_readback": {
            "error": "claim_not_found"}},
    ))
    db.commit()


def _prepare_resume(db_session, monkeypatch, *, claim_absent):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _seed_v7_zero_write(db_session)
    _seed_v8_preclaim_failure(db_session, monkeypatch)
    _patch_scope(db_session, monkeypatch)
    monkeypatch.setattr(
        campaign_policy_service, "require_policy",
        lambda: {"_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        recovery.v7, "_target_rows",
        lambda *_a, **_k: (_signup_rows(), None))
    monkeypatch.setattr(
        recovery.v7, "_discount_scope",
        lambda *_a, **_k: (_discount_rows(), None))
    phases = []

    def fake_web(_db, *, payload, timeout_s=2400):
        phase = payload["phase"]
        phases.append(phase)
        result = _web_result(payload, phase=phase)
        if phase == "inspect":
            result["resume_evidence"] = {
                "ok": True, **recovery.EXPECTED_RESUME_EVIDENCE}
            result["recovery_evidence"] = {
                "ok": True,
                "v6_attempt_id": recovery.v7.V6_ATTEMPT_ID,
                "error_artifact_sha256": recovery.v7.RECOVERY_EVIDENCE[
                    "v6_error_artifact_sha256"],
                "fresh_product_export_sha256": recovery.v7.RECOVERY_EVIDENCE[
                    "fresh_product_export_sha256"],
            }
            result["v8_claim_absent"] = claim_absent
            result["v8_claim_sha256"] = None if claim_absent else "b" * 64
        elif phase == "commit":
            result["checkpoints"] = recovery.EXPECTED_COMMIT_CHECKPOINTS
        return result

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v8", fake_web)
    return phases


def test_v8_preclaim_resume_reuses_same_attempt_once(db_session, monkeypatch):
    phases = _prepare_resume(db_session, monkeypatch, claim_absent=True)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_preclaim_v3",
        confirmation=recovery.PRECLAIM_RESUME_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert result["ok"] is True
    assert result["attempt_id"] == recovery.PRECLAIM_ATTEMPT_ID
    assert phases == ["inspect", "commit", "readback"]
    attempts = recovery._attempts(db_session)
    assert len(attempts) == 1
    assert attempts[0].state == "completed"
    assert attempts[0].automatic_retry_allowed is False


def test_v8_preclaim_resume_stops_when_agent_claim_exists(
        db_session, monkeypatch):
    phases = _prepare_resume(db_session, monkeypatch, claim_absent=False)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_preclaim_v3",
        confirmation=recovery.PRECLAIM_RESUME_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert result["ok"] is False
    assert result["error"] == "plan8_final_v8_preclaim_resume_not_proven_safe"
    assert phases == ["inspect"]
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.state == "unknown_no_retry"
    assert attempt.scope_sha256 == recovery.PRECLAIM_SCOPE_SHA256


def test_v8_cli_accepts_only_exact_mode_confirmation(monkeypatch):
    payload = {
        "workflow_key": recovery.WORKFLOW_KEY, "plan_id": 8,
        "expected_status": "alarmed", "recovery_version": 8,
        "mode": "execute", "confirmation": recovery.EXECUTE_CONFIRMATION,
        "target_scope_sha256": recovery.EXPECTED_TARGET_SCOPE_SHA256,
    }
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["recovery_version"] == 8
    payload["confirmation"] = recovery.READBACK_CONFIRMATION
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    try:
        cli._read_payload()
    except ValueError:
        pass
    else:
        raise AssertionError("V8 accepted the wrong execution confirmation")
    payload["mode"] = "resume_preclaim_v3"
    payload["confirmation"] = recovery.PRECLAIM_RESUME_CONFIRMATION
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == "resume_preclaim_v3"
    payload["mode"] = "resume_claimed_preupload_v4"
    payload["confirmation"] = recovery.CLAIMED_PREUPLOAD_RESUME_CONFIRMATION
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v4")
    payload["mode"] = "resume_claimed_preupload_v5"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_POST_READBACK_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v5")


def _seed_v8_claimed_preupload_failure(db, monkeypatch):
    base = recovery._fixed_manifest(
        _signup_rows(), _discount_rows(), recovery.EXPECTED_POLICY_SHA256)
    manifest = {**base, "inspection_baseline": {
        "inspect_scope_sha256": recovery.v6._hash(base)}}
    scope = recovery.v6._hash(manifest)
    monkeypatch.setattr(recovery, "CLAIMED_PREUPLOAD_SCOPE_SHA256", scope)
    claim_sha = "c" * 64
    monkeypatch.setattr(recovery, "CLAIMED_PREUPLOAD_CLAIM_SHA256", claim_sha)
    db.add(CampaignExecutionAttempt(
        id=recovery.PRECLAIM_ATTEMPT_ID, plan_id=8,
        workflow_key=recovery.WORKFLOW_KEY, operation=recovery.OPERATION,
        scope_sha256=scope, state="failed_no_retry", write_claimed=True,
        platform_write_observed=False, automatic_retry_allowed=False,
        request_id=recovery.PRECLAIM_REQUEST_ID,
        last_step=recovery.CLAIMED_PREUPLOAD_LAST_STEP,
        error_code=recovery.CLAIMED_PREUPLOAD_ERROR_CODE,
        web_agent_job_id="job2", result_summary={
            "manifest": manifest,
            "commit": {
                "platform_write": False,
                "reservation_consumed": True,
                "claim_created": True,
                "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
                "web_agent_error": recovery.CLAIMED_PREUPLOAD_ERROR_CODE,
                "patched_record_ids": [],
                "published_record_ids": [],
                "discount_pairs_written": [],
            },
        },
    ))
    db.commit()
    return manifest, scope, claim_sha


def test_v8_claimed_preupload_resume_reuses_same_claim_and_attempt(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _patch_scope(db_session, monkeypatch)
    monkeypatch.setattr(
        recovery.campaign_policy_service, "require_policy",
        lambda: {"_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        recovery.v7, "_target_rows",
        lambda *_a, **_k: (_signup_rows(), None))
    monkeypatch.setattr(
        recovery.v7, "_discount_scope",
        lambda *_a, **_k: (_discount_rows(), None))
    manifest, scope, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    assert recovery.v6._hash(frozen_base) == manifest[
        "inspection_baseline"]["inspect_scope_sha256"]
    inspect_scope = {"bound": "same-claim"}
    token = "reservation-token-preupload-v4"
    phases = []

    def fake_preupload_web(_db, *, payload, timeout_s=2400):
        phases.append(payload["phase"])
        assert payload["scope_sha256"] == scope
        if payload["phase"] == "inspect":
            return {
                "ok": True,
                "platform_write": False,
                "claim_created": True,
                "resume_claim_sha256": claim_sha,
                "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
                "inspect_scope": inspect_scope,
                "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
                "reservation_token": token,
                "lease_expires_at_epoch": 4102444800.0,
                "web_agent_job_id": "job-v4-inspect",
            }
        raise AssertionError("commit is stubbed below")

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "attempt_id": kwargs["attempt"].id}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume",
        fake_preupload_web)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v4",
        confirmation=recovery.CLAIMED_PREUPLOAD_RESUME_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert phases == ["inspect"]
    assert captured["manifest"] == manifest
    assert captured["manifest_sha"] == scope
    assert captured["reservation_token"] == token
    assert captured["commit_phase"] == "resume_preupload_commit"
    assert captured["resume_claim_sha256"] == claim_sha
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.state == "write_claimed"
    assert attempt.platform_write_observed is False
    assert attempt.automatic_retry_allowed is False
    assert attempt.result_summary["claimed_preupload_resume"][
        "source_claim_sha256"] == claim_sha


def test_v8_preupload_claim_verifier_rejects_changed_claim(
        db_session, monkeypatch):
    manifest, scope, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    attempt.state = "write_claimed"
    attempt.result_summary = {**attempt.result_summary,
        "claimed_preupload_resume": {
            "source_claim_sha256": claim_sha,
            "inspect_scope_sha256": "d" * 64,
            "reservation_token_sha256": "e" * 64,
            "reservation_expires_at_epoch": 4102444800.0,
        }}
    db_session.commit()
    result = recovery.verify_plan8_final_v8_preupload_claim(
        db_session, attempt_id=attempt.id,
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        operation=recovery.OPERATION, scope_sha256=scope,
        inspect_scope_sha256="d" * 64,
        reservation_token_sha256="e" * 64,
        resume_claim_sha256=claim_sha)
    assert result["ok"] is True
    result = recovery.verify_plan8_final_v8_preupload_claim(
        db_session, attempt_id=attempt.id,
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        operation=recovery.OPERATION, scope_sha256=scope,
        inspect_scope_sha256="d" * 64,
        reservation_token_sha256="e" * 64,
        resume_claim_sha256="f" * 64)
    assert result["ok"] is False


def test_v8_claimed_preupload_v5_accepts_only_frozen_zero_write_readback(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _patch_scope(db_session, monkeypatch)
    monkeypatch.setattr(
        recovery.campaign_policy_service, "require_policy",
        lambda: {"_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        recovery.v7, "_target_rows",
        lambda *_a, **_k: (_signup_rows(), None))
    monkeypatch.setattr(
        recovery.v7, "_discount_scope",
        lambda *_a, **_k: (_discount_rows(), None))
    manifest, scope, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    readback = {
        "record_count": 6, "sku_count": 70, "custom_sku_count": 18,
        "missing_sku_ids": list(recovery.POST_READBACK_MISSING_SKU_IDS),
        "unexpected_sku_ids": [], "discount_rows": [],
        "web_agent_job_id": "job3",
    }
    attempt.last_step = "readback_not_complete"
    attempt.error_code = "post_submit_readback_not_complete"
    attempt.web_agent_job_id = "job3"
    attempt.result_summary = {**attempt.result_summary,
                              "last_readback": readback}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "POST_READBACK_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "POST_READBACK_DETAIL_SHA256", recovery.v6._hash(readback))
    inspect_scope = {"bound": "same-claim-after-readback"}
    token = "reservation-token-preupload-v5"

    def fake_preupload_web(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": claim_sha,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": token,
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v5-inspect",
        }

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "attempt_id": kwargs["attempt"].id}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume",
        fake_preupload_web)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v5",
        confirmation=recovery.CLAIMED_PREUPLOAD_POST_READBACK_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert captured["commit_phase"] == "resume_preupload_commit"
    assert captured["resume_claim_sha256"] == claim_sha
    assert captured["manifest_sha"] == scope
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.state == "write_claimed"
    assert attempt.last_step == (
        "platform_write_claim_claimed_preupload_resume_v5")
    assert attempt.automatic_retry_allowed is False


def test_v8_inspection_preserves_web_agent_failure_diagnostics():
    manifest = recovery._fixed_manifest(
        _signup_rows(), _discount_rows(), recovery.EXPECTED_POLICY_SHA256)
    scope = recovery.v6._hash(manifest)
    ok, detail = recovery.validate_inspection({
        "ok": False,
        "error": "plan8_v6_bound_draft_editor_not_unique:{\"match_count\":2}",
        "step": "bound_draft_price_readback",
        "facts": {"editor_binding": {"match_count": 2}},
        "claim_created": False,
        "need_scan": False,
        "v8_claim_absent": True,
        "scope_sha256": scope,
    }, manifest, scope)
    assert ok is False
    assert detail["web_agent_step"] == "bound_draft_price_readback"
    assert detail["web_agent_facts"]["editor_binding"]["match_count"] == 2
    assert detail["web_agent_claim_created"] is False

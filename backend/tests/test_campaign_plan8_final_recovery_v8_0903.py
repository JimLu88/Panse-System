import json
from io import BytesIO

import pytest

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


def test_web_agent_v9_uses_dedicated_claim_bound_path(monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-v9"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_a, **_k: {"result": {"ok": True}})
    result = web_agent_service.recover_plan8_final_v8_preupload_resume_v9(
        object(), payload={"phase": "inspect"})
    assert result["ok"] is True
    assert captured["path"].endswith("preupload-resume-v9")


def test_web_agent_v10_uses_dedicated_claim_bound_path(monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-v10"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_a, **_k: {"result": {"ok": True}})
    result = web_agent_service.recover_plan8_final_v8_preupload_resume_v10(
        object(), payload={"phase": "inspect"})
    assert result["ok"] is True
    assert captured["path"].endswith("preupload-resume-v10")


def test_web_agent_v12_uses_dedicated_lazy_import_path(monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-v12"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_a, **_k: {"result": {"ok": True}})
    result = web_agent_service.recover_plan8_final_v8_preupload_resume_v12(
        object(), payload={"phase": "inspect"})
    assert result["ok"] is True
    assert captured["path"].endswith("preupload-resume-v12")


def test_web_agent_v14_uses_dedicated_semantic_modal_path(monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-v14"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_a, **_k: {"result": {"ok": True}})
    result = web_agent_service.recover_plan8_final_v8_preupload_resume_v14(
        object(), payload={"phase": "inspect"})
    assert result["ok"] is True
    assert captured["path"].endswith("preupload-resume-v14")


def test_web_agent_v15_uses_dedicated_editor_identity_path(monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-v15"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_a, **_k: {"result": {"ok": True}})
    result = web_agent_service.recover_plan8_final_v8_preupload_resume_v15(
        object(), payload={"phase": "inspect"})
    assert result["ok"] is True
    assert captured["path"].endswith("preupload-resume-v15")


def test_web_agent_v16_uses_dedicated_nested_modal_path(monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-v16"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_a, **_k: {"result": {"ok": True}})
    result = web_agent_service.recover_plan8_final_v8_preupload_resume_v16(
        object(), payload={"phase": "inspect"})
    assert result["ok"] is True
    assert captured["path"].endswith("preupload-resume-v16")


def test_web_agent_v17_uses_dedicated_moban_variant_path(monkeypatch):
    captured = {}

    def fake_post(_db, path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"ok": True, "job": "job-v17"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(web_agent_service, "wait_job",
                        lambda *_a, **_k: {"result": {"ok": True}})
    result = web_agent_service.recover_plan8_final_v8_preupload_resume_v17(
        object(), payload={"phase": "inspect"})
    assert result["ok"] is True
    assert captured["path"].endswith("preupload-resume-v17")


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
    payload["mode"] = "resume_claimed_preupload_v6"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_LEASE_SCOPE_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v6")
    payload["mode"] = "resume_claimed_preupload_v7"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_BUSY_WAIT_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v7")
    payload["mode"] = "resume_claimed_preupload_v8"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_LEASE_EXPIRY_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v8")
    payload["mode"] = "resume_claimed_preupload_v9"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_DIALOG_FIX_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v9")
    payload["mode"] = "resume_claimed_preupload_v10"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_CAMPAIGN_GUARD_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v10")
    payload["mode"] = "resume_claimed_preupload_v11"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_CLAIM_VERIFY_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v11")
    payload["mode"] = "resume_claimed_preupload_v12"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_LAZY_IMPORT_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v12")
    payload["mode"] = "resume_claimed_preupload_v13"
    payload["confirmation"] = (
        recovery.CLAIMED_PREUPLOAD_ALLOWLIST_CONFIRMATION)
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": BytesIO(json.dumps(payload).encode("utf-8"))})())
    assert json.loads(cli._read_payload())["mode"] == (
        "resume_claimed_preupload_v13")


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


@pytest.mark.parametrize(
    ("mode", "confirmation", "busy_first", "expected_step"), [
        ("resume_claimed_preupload_v6",
         recovery.CLAIMED_PREUPLOAD_LEASE_SCOPE_CONFIRMATION, False,
         "platform_write_claim_claimed_preupload_resume_v6"),
        ("resume_claimed_preupload_v7",
         recovery.CLAIMED_PREUPLOAD_BUSY_WAIT_CONFIRMATION, True,
         "platform_write_claim_claimed_preupload_resume_v7"),
    ])
def test_v8_claimed_preupload_after_lease_drift_accepts_exact_state(
        db_session, monkeypatch, mode, confirmation, busy_first,
        expected_step):
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
    inspection = {
        "resume_claim_sha256": claim_sha,
        "inspect_scope_sha256": "d" * 64,
        "reservation_token_sha256": "e" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "platform_write": False, "claim_created": False,
        "web_agent_error": "plan8_v8_state_changed_before_claim",
        "patched_record_ids": [], "published_record_ids": [],
        "discount_pairs_written": [],
    }
    attempt.last_step = "plan8_final_v8_commit"
    attempt.error_code = "plan8_v8_state_changed_before_claim"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        **attempt.result_summary, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V5_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V5_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V5_COMMIT_SHA256", recovery.v6._hash(commit))
    inspect_scope = {"bound": "same-claim-new-lease"}
    token = "reservation-token-preupload-v6"
    calls = []

    def fake_preupload_web(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        calls.append(payload)
        if busy_first and len(calls) == 1:
            return {
                "ok": False, "error": "taobao_profile_busy",
                "step": "preupload_resume_busy", "busy": True,
                "pre_write_busy": True, "retry_safe": True,
                "platform_write": False, "claim_created": True,
            }
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": claim_sha,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": token,
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v6-inspect",
        }

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "attempt_id": kwargs["attempt"].id}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume",
        fake_preupload_web)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    monkeypatch.setattr(recovery.time, "sleep", lambda _seconds: None)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode=mode, confirmation=confirmation,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert captured["commit_phase"] == "resume_preupload_commit"
    assert captured["resume_claim_sha256"] == claim_sha
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.last_step == expected_step
    assert len(calls) == (2 if busy_first else 1)
    assert attempt.platform_write_observed is False
    assert attempt.automatic_retry_allowed is False


def test_v8_lease_expiry_resume_accepts_only_frozen_zero_write_stop(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    manifest, _, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": claim_sha,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "b" * 64,
        "lease_expires_at_epoch": 1788469562.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "platform_write": False, "claim_created": False,
        "web_agent_error": "plan8_v8_erp_claim_not_verified",
        "web_agent_detail": {
            "ok": False,
            "error": "erp_preupload_claim_verify_unavailable",
            "error_type": "HTTPError",
        },
        "patched_record_ids": [], "published_record_ids": [],
        "discount_pairs_written": [],
    }
    attempt.state = "failed_no_retry"
    attempt.write_claimed = True
    attempt.platform_write_observed = False
    attempt.automatic_retry_allowed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "plan8_final_v8_commit"
    attempt.error_code = "plan8_v8_erp_claim_not_verified"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest,
        "inspection": inspection,
        "commit": commit,
    }
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V7_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V7_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V7_COMMIT_SHA256", recovery.v6._hash(commit))

    ok, detail = recovery._validate_claimed_preupload_after_lease_expiry_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = True
    ok, _ = recovery._validate_claimed_preupload_after_lease_expiry_attempt(
        attempt)
    assert ok is False


def test_v9_dialog_mismatch_resume_accepts_only_frozen_zero_write_stop(
        db_session, monkeypatch):
    manifest, _, old_claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": old_claim_sha,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "b" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "platform_write": False, "reservation_consumed": True,
        "claim_created": True, "last_checkpoint": "draft_patch_terminal",
        "web_agent_error": "plan8_v8_unknown_outcome_no_retry",
        "patched_record_ids": [], "published_record_ids": [],
        "discount_pairs_written": [],
    }
    attempt.state = "failed_no_retry"
    attempt.platform_write_observed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "draft_patch_terminal"
    attempt.error_code = "plan8_v8_unknown_outcome_no_retry"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V8_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V8_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V8_COMMIT_SHA256", recovery.v6._hash(commit))

    ok, detail = recovery._validate_claimed_preupload_after_dialog_mismatch_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = True
    ok, _ = recovery._validate_claimed_preupload_after_dialog_mismatch_attempt(
        attempt)
    assert ok is False


def test_v9_dialog_mismatch_resume_uses_new_claim_and_endpoint(
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
    manifest, _, old_claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": old_claim_sha,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "b" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "platform_write": False, "reservation_consumed": True,
        "claim_created": True, "last_checkpoint": "draft_patch_terminal",
        "web_agent_error": "plan8_v8_unknown_outcome_no_retry",
        "patched_record_ids": [], "published_record_ids": [],
        "discount_pairs_written": [],
    }
    attempt.state = "failed_no_retry"
    attempt.platform_write_observed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "draft_patch_terminal"
    attempt.error_code = "plan8_v8_unknown_outcome_no_retry"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V8_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V8_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V8_COMMIT_SHA256", recovery.v6._hash(commit))
    new_claim_sha = "9" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V9_CLAIM_SHA256", new_claim_sha)
    inspect_scope = {"bound": "v9-modal-fix"}

    def fake_v9(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": new_claim_sha,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": "v9-reservation-token",
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v9-inspect",
        }

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume_v9",
        fake_v9)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v9",
        confirmation=recovery.CLAIMED_PREUPLOAD_DIALOG_FIX_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert captured["resume_claim_sha256"] == new_claim_sha
    assert captured["use_preupload_v9_endpoint"] is True
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.last_step == (
        "platform_write_claim_claimed_preupload_resume_v9")
    assert attempt.result_summary["claimed_preupload_resume"][
        "source_claim_sha256"] == new_claim_sha


def test_v9_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v9",
        confirmation=recovery.CLAIMED_PREUPLOAD_DIALOG_FIX_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v9"


def test_v10_guard_stop_accepts_only_frozen_zero_write_state(
        db_session, monkeypatch):
    manifest, _, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V9_CLAIM_SHA256", claim_sha)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": claim_sha,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "b" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "platform_write": None, "claim_created": False,
        "web_agent_error": "plan8_v6_bound_draft_campaign_guard_failed",
        "patched_record_ids": [], "published_record_ids": [],
        "discount_pairs_written": [],
    }
    attempt.state = "unknown_no_retry"
    attempt.platform_write_observed = None
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "plan8_final_v8_commit"
    attempt.error_code = "plan8_v6_bound_draft_campaign_guard_failed"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V9_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V9_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V9_COMMIT_SHA256", recovery.v6._hash(commit))

    ok, detail = recovery._validate_claimed_preupload_after_campaign_guard_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = False
    ok, _ = recovery._validate_claimed_preupload_after_campaign_guard_attempt(
        attempt)
    assert ok is False


def test_v10_campaign_guard_resume_uses_exact_claim_and_endpoint(
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
    manifest, _, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V9_CLAIM_SHA256", claim_sha)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    old_inspection = {
        "resume_claim_sha256": claim_sha,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "b" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    old_commit = {
        "platform_write": None, "claim_created": False,
        "web_agent_error": "plan8_v6_bound_draft_campaign_guard_failed",
        "patched_record_ids": [], "published_record_ids": [],
        "discount_pairs_written": [],
    }
    attempt.state = "unknown_no_retry"
    attempt.platform_write_observed = None
    attempt.last_step = "plan8_final_v8_commit"
    attempt.error_code = "plan8_v6_bound_draft_campaign_guard_failed"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": old_inspection,
        "commit": old_commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V9_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V9_INSPECTION_SHA256", recovery.v6._hash(old_inspection))
    monkeypatch.setattr(
        recovery, "V9_COMMIT_SHA256", recovery.v6._hash(old_commit))
    inspect_scope = {"bound": "v10-guard-settle"}

    def fake_v10(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": claim_sha,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": "v10-reservation-token",
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v10-inspect",
        }

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume_v10",
        fake_v10)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v10",
        confirmation=recovery.CLAIMED_PREUPLOAD_CAMPAIGN_GUARD_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert captured["resume_claim_sha256"] == claim_sha
    assert captured["use_preupload_v10_endpoint"] is True
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.last_step == (
        "platform_write_claim_claimed_preupload_resume_v10")


def test_v10_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v10",
        confirmation=recovery.CLAIMED_PREUPLOAD_CAMPAIGN_GUARD_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v10"


def test_v11_claim_verifier_uses_v9_claim_for_v10_and_v11_steps(
        db_session, monkeypatch):
    _, scope, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    v9_claim = "9" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V9_CLAIM_SHA256", v9_claim)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    attempt.state = "write_claimed"
    for step in (
            "platform_write_claim_claimed_preupload_resume_v10",
            "platform_write_claim_claimed_preupload_resume_v11"):
        attempt.last_step = step
        attempt.result_summary = {**attempt.result_summary,
            "claimed_preupload_resume": {
                "source_claim_sha256": v9_claim,
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
            resume_claim_sha256=v9_claim)
        assert result["ok"] is True, (step, result)


def test_v11_accepts_only_frozen_v10_zero_write_claim_rejection(
        db_session, monkeypatch):
    manifest, _, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V9_CLAIM_SHA256", claim_sha)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": claim_sha,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "b" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "platform_write": False, "claim_created": False,
        "web_agent_error": "plan8_v8_erp_claim_not_verified",
        "web_agent_detail": {
            "error": "erp_preupload_claim_verify_rejected",
            "http_status": 409,
        },
        "patched_record_ids": [], "published_record_ids": [],
        "discount_pairs_written": [],
    }
    attempt.state = "failed_no_retry"
    attempt.platform_write_observed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "plan8_final_v8_commit"
    attempt.error_code = "plan8_v8_erp_claim_not_verified"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V10_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V10_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V10_COMMIT_SHA256", recovery.v6._hash(commit))

    ok, detail = recovery._validate_claimed_preupload_after_claim_verify_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = True
    ok, _ = recovery._validate_claimed_preupload_after_claim_verify_attempt(
        attempt)
    assert ok is False


def test_v11_resume_reuses_strict_v10_endpoint_and_v9_claim(
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
    manifest, _, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V9_CLAIM_SHA256", claim_sha)
    monkeypatch.setattr(
        recovery, "_validate_claimed_preupload_after_claim_verify_attempt",
        lambda _attempt: (True, {}))
    inspect_scope = {"bound": "v11-claim-verifier-fix"}

    def fake_v10(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": claim_sha,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": "v11-reservation-token",
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v11-inspect",
        }

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume_v10",
        fake_v10)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v11",
        confirmation=recovery.CLAIMED_PREUPLOAD_CLAIM_VERIFY_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert captured["resume_claim_sha256"] == claim_sha
    assert captured["use_preupload_v10_endpoint"] is True
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.last_step == (
        "platform_write_claim_claimed_preupload_resume_v11")


def test_v11_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v11",
        confirmation=recovery.CLAIMED_PREUPLOAD_CLAIM_VERIFY_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v11"


def test_v12_claim_verifier_uses_v11_claim_for_v12_and_v13_steps(
        db_session, monkeypatch):
    _, scope, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    v11_claim = "b" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V11_CLAIM_SHA256", v11_claim)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    attempt.state = "write_claimed"
    for step in (
            "platform_write_claim_claimed_preupload_resume_v12",
            "platform_write_claim_claimed_preupload_resume_v13"):
        attempt.last_step = step
        attempt.result_summary = {**attempt.result_summary,
            "claimed_preupload_resume": {
                "source_claim_sha256": v11_claim,
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
            resume_claim_sha256=v11_claim)
        assert result["ok"] is True, (step, result)


def test_v12_accepts_only_frozen_v11_lazy_import_no_write_state(
        db_session, monkeypatch):
    manifest, _, claim_sha = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V9_CLAIM_SHA256", claim_sha)
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V11_CLAIM_SHA256", "b" * 64)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": claim_sha,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "b" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "platform_write": False, "claim_created": True,
        "reservation_consumed": True,
        "last_checkpoint": "draft_patch_terminal",
        "web_agent_error": "plan8_v8_unknown_outcome_no_retry",
        "web_agent_detail": {
            "draft_patch": {
                "ok": False,
                "error": "plan8_v6_update_dialog_not_exact",
                "submitted": False,
            },
        },
        "patched_record_ids": [], "published_record_ids": [],
        "discount_pairs_written": [],
    }
    attempt.state = "failed_no_retry"
    attempt.platform_write_observed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "draft_patch_terminal"
    attempt.error_code = "plan8_v8_unknown_outcome_no_retry"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V11_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V11_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V11_COMMIT_SHA256", recovery.v6._hash(commit))

    ok, detail = recovery._validate_claimed_preupload_after_lazy_import_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = True
    ok, _ = recovery._validate_claimed_preupload_after_lazy_import_attempt(
        attempt)
    assert ok is False


def test_v12_resume_uses_new_claim_and_dedicated_endpoint(
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
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    v11_claim = "b" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V11_CLAIM_SHA256", v11_claim)
    monkeypatch.setattr(
        recovery, "_validate_claimed_preupload_after_lazy_import_attempt",
        lambda _attempt: (True, {}))
    inspect_scope = {"bound": "v12-lazy-import-fix"}

    def fake_v12(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": v11_claim,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": "v12-reservation-token",
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v12-inspect",
        }

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume_v12",
        fake_v12)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v12",
        confirmation=recovery.CLAIMED_PREUPLOAD_LAZY_IMPORT_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert captured["resume_claim_sha256"] == v11_claim
    assert captured["use_preupload_v12_endpoint"] is True
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.last_step == (
        "platform_write_claim_claimed_preupload_resume_v12")


def test_v12_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v12",
        confirmation=recovery.CLAIMED_PREUPLOAD_LAZY_IMPORT_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v12"


def test_v13_accepts_only_frozen_v12_zero_write_allowlist_stop(
        db_session, monkeypatch):
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    v11_claim = "b" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V11_CLAIM_SHA256", v11_claim)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": v11_claim,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "e" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "step": None, "platform_write": False, "scope_sha256": None,
        "inspection_baseline": None, "discount_rows_written": None,
        "draft_records_updated": None, "draft_records_published": None,
        "reservation_consumed": None,
        "discount_pairs_written": [],
        "discount_pairs_already_correct": [],
        "patched_record_ids": [], "published_record_ids": [],
        "checkpoints": None, "web_agent_job_id": "job2",
        "v8_checkpoint_order_ok": False,
        "web_agent_error": "plan8_v8_erp_claim_not_verified",
        "web_agent_error_code": None, "web_agent_status": None,
        "last_checkpoint": None, "claim_created": False,
        "different_fields": [],
        "web_agent_detail": {
            "ok": False,
            "error": "erp_preupload_claim_verify_request_invalid",
        },
        "candidate_price_evidence": None,
    }
    attempt.state = "failed_no_retry"
    attempt.platform_write_observed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "plan8_final_v8_commit"
    attempt.error_code = "plan8_v8_erp_claim_not_verified"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V12_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V12_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V12_COMMIT_SHA256", recovery.v6._hash(commit))

    ok, detail = recovery._validate_claimed_preupload_after_allowlist_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = True
    ok, _ = recovery._validate_claimed_preupload_after_allowlist_attempt(
        attempt)
    assert ok is False


def test_v13_resume_reuses_v11_claim_and_fixed_v12_endpoint(
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
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    v11_claim = "b" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V11_CLAIM_SHA256", v11_claim)
    monkeypatch.setattr(
        recovery, "_validate_claimed_preupload_after_allowlist_attempt",
        lambda _attempt: (True, {}))
    inspect_scope = {"bound": "v13-claim-allowlist-fix"}

    def fake_v12(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": v11_claim,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": "v13-reservation-token",
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v13-inspect",
        }

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume_v12",
        fake_v12)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v13",
        confirmation=recovery.CLAIMED_PREUPLOAD_ALLOWLIST_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert captured["resume_claim_sha256"] == v11_claim
    assert captured["use_preupload_v12_endpoint"] is True
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.last_step == (
        "platform_write_claim_claimed_preupload_resume_v13")


def test_v13_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v13",
        confirmation=recovery.CLAIMED_PREUPLOAD_ALLOWLIST_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v13"


def test_v14_claim_verifier_uses_v13_claim_only_for_v14_step(
        db_session, monkeypatch):
    _, scope, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    v13_claim = "8" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V13_CLAIM_SHA256", v13_claim)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    attempt.state = "write_claimed"
    attempt.last_step = "platform_write_claim_claimed_preupload_resume_v14"
    attempt.result_summary = {**attempt.result_summary,
        "claimed_preupload_resume": {
            "source_claim_sha256": v13_claim,
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
        resume_claim_sha256=v13_claim)
    assert result["ok"] is True, result


def test_v14_accepts_only_frozen_v13_pre_file_modal_stop(
        db_session, monkeypatch):
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    v11_claim = "b" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V11_CLAIM_SHA256", v11_claim)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": v11_claim,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "e" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "step": None, "platform_write": False, "scope_sha256": None,
        "inspection_baseline": None, "discount_rows_written": None,
        "draft_records_updated": None, "draft_records_published": None,
        "reservation_consumed": True,
        "discount_pairs_written": [],
        "discount_pairs_already_correct": [],
        "patched_record_ids": [], "published_record_ids": [],
        "checkpoints": None, "web_agent_job_id": "job2",
        "v8_checkpoint_order_ok": False,
        "web_agent_error": "plan8_v8_unknown_outcome_no_retry",
        "web_agent_error_code": None, "web_agent_status": None,
        "last_checkpoint": "draft_patch_terminal", "claim_created": True,
        "different_fields": [], "web_agent_detail": None,
        "candidate_price_evidence": None,
    }
    attempt.state = "failed_no_retry"
    attempt.write_claimed = True
    attempt.platform_write_observed = False
    attempt.automatic_retry_allowed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "draft_patch_terminal"
    attempt.error_code = "plan8_v8_unknown_outcome_no_retry"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V13_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V13_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V13_COMMIT_SHA256", recovery.v6._hash(commit))

    ok, detail = recovery._validate_claimed_preupload_after_semantic_modal_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = True
    ok, _ = recovery._validate_claimed_preupload_after_semantic_modal_attempt(
        attempt)
    assert ok is False


def test_v14_resume_uses_v13_claim_and_semantic_modal_endpoint(
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
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    v13_claim = "8" * 64
    monkeypatch.setattr(
        recovery, "CLAIMED_PREUPLOAD_V13_CLAIM_SHA256", v13_claim)
    monkeypatch.setattr(
        recovery, "_validate_claimed_preupload_after_semantic_modal_attempt",
        lambda _attempt: (True, {}))
    inspect_scope = {"bound": "v14-semantic-modal-fix"}

    def fake_v14(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": v13_claim,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": "v14-reservation-token",
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v14-inspect",
        }

    captured = {}

    def fake_commit(_db, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume_v14",
        fake_v14)
    monkeypatch.setattr(recovery, "_commit_and_readback", fake_commit)
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v14",
        confirmation=recovery.CLAIMED_PREUPLOAD_SEMANTIC_MODAL_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)

    assert result["ok"] is True, result
    assert captured["resume_claim_sha256"] == v13_claim
    assert captured["use_preupload_v14_endpoint"] is True
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.last_step == (
        "platform_write_claim_claimed_preupload_resume_v14")


def test_v14_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v14",
        confirmation=recovery.CLAIMED_PREUPLOAD_SEMANTIC_MODAL_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v14"


def test_v15_accepts_only_frozen_v14_read_only_identity_stop(
        db_session, monkeypatch):
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": recovery.CLAIMED_PREUPLOAD_V13_CLAIM_SHA256,
        "inspect_scope_sha256": "a" * 64,
        "reservation_token_sha256": "e" * 64,
        "lease_expires_at_epoch": 4102444800.0,
        "web_agent_job_id": "job1",
    }
    commit = {
        "step": None, "platform_write": None,
        "scope_sha256": recovery.CLAIMED_PREUPLOAD_SCOPE_SHA256,
        "inspection_baseline": None, "discount_rows_written": None,
        "draft_records_updated": None, "draft_records_published": None,
        "reservation_consumed": None, "discount_pairs_written": [],
        "discount_pairs_already_correct": [], "patched_record_ids": [],
        "published_record_ids": [], "checkpoints": None,
        "web_agent_job_id": "job2", "v8_checkpoint_order_ok": False,
        "web_agent_error": "plan8_v6_bound_draft_editor_identity_mismatch",
        "web_agent_error_code": None, "web_agent_status": None,
        "last_checkpoint": None, "claim_created": False,
        "different_fields": [], "web_agent_detail": None,
        "candidate_price_evidence": None,
    }
    attempt.state = "unknown_no_retry"
    attempt.write_claimed = True
    attempt.platform_write_observed = None
    attempt.automatic_retry_allowed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "plan8_final_v8_commit"
    attempt.error_code = "plan8_v6_bound_draft_editor_identity_mismatch"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(
        recovery, "V14_RESULT_SUMMARY_SHA256",
        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(
        recovery, "V14_INSPECTION_SHA256", recovery.v6._hash(inspection))
    monkeypatch.setattr(
        recovery, "V14_COMMIT_SHA256", recovery.v6._hash(commit))

    ok, detail = recovery._validate_claimed_preupload_after_editor_identity_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = False
    ok, _ = recovery._validate_claimed_preupload_after_editor_identity_attempt(
        attempt)
    assert ok is False


def test_v15_resume_uses_v13_claim_and_editor_identity_endpoint(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _patch_scope(db_session, monkeypatch)
    monkeypatch.setattr(
        recovery.campaign_policy_service, "require_policy",
        lambda: {"_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        recovery.v7, "_target_rows", lambda *_a, **_k: (_signup_rows(), None))
    monkeypatch.setattr(
        recovery.v7, "_discount_scope", lambda *_a, **_k: (_discount_rows(), None))
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(
        recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    monkeypatch.setattr(
        recovery, "_validate_claimed_preupload_after_editor_identity_attempt",
        lambda _attempt: (True, {}))
    inspect_scope = {"bound": "v15-editor-identity-format-fix"}

    def fake_v15(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": recovery.CLAIMED_PREUPLOAD_V13_CLAIM_SHA256,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": "v15-reservation-token",
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v15-inspect",
        }

    captured = {}
    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume_v15",
        fake_v15)
    monkeypatch.setattr(
        recovery, "_commit_and_readback",
        lambda _db, **kwargs: captured.update(kwargs) or {"ok": True})
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v15",
        confirmation=recovery.CLAIMED_PREUPLOAD_EDITOR_IDENTITY_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert result["ok"] is True, result
    assert captured["resume_claim_sha256"] == (
        recovery.CLAIMED_PREUPLOAD_V13_CLAIM_SHA256)
    assert captured["use_preupload_v15_endpoint"] is True
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    assert attempt.last_step == (
        "platform_write_claim_claimed_preupload_resume_v15")


def test_v15_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v15",
        confirmation=recovery.CLAIMED_PREUPLOAD_EDITOR_IDENTITY_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v15"


def test_v16_validator_accepts_only_exact_v15_zero_write_state(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _patch_scope(db_session, monkeypatch)
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    attempt = db_session.get(
        CampaignExecutionAttempt, recovery.PRECLAIM_ATTEMPT_ID)
    inspection = {
        "resume_claim_sha256": recovery.CLAIMED_PREUPLOAD_V13_CLAIM_SHA256,
    }
    commit = {
        "platform_write": False, "reservation_consumed": True,
        "claim_created": True, "last_checkpoint": "draft_patch_terminal",
        "web_agent_error": "plan8_v8_unknown_outcome_no_retry",
        "web_agent_detail": None, "patched_record_ids": [],
        "published_record_ids": [], "discount_pairs_written": [],
    }
    attempt.state = "failed_no_retry"
    attempt.write_claimed = True
    attempt.platform_write_observed = False
    attempt.automatic_retry_allowed = False
    attempt.request_id = recovery.PRECLAIM_REQUEST_ID
    attempt.last_step = "draft_patch_terminal"
    attempt.error_code = "plan8_v8_unknown_outcome_no_retry"
    attempt.web_agent_job_id = "job2"
    attempt.result_summary = {
        "manifest": manifest, "inspection": inspection, "commit": commit}
    db_session.commit()
    monkeypatch.setattr(recovery, "V15_RESULT_SUMMARY_SHA256",
                        recovery.v6._hash(attempt.result_summary))
    monkeypatch.setattr(recovery, "V15_INSPECTION_SHA256",
                        recovery.v6._hash(inspection))
    monkeypatch.setattr(recovery, "V15_COMMIT_SHA256",
                        recovery.v6._hash(commit))
    ok, detail = recovery._validate_claimed_preupload_after_nested_modal_attempt(
        attempt)
    assert ok is True, detail
    attempt.platform_write_observed = None
    assert recovery._validate_claimed_preupload_after_nested_modal_attempt(
        attempt)[0] is False


def test_v16_resumes_only_exact_nested_modal_state(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _patch_scope(db_session, monkeypatch)
    monkeypatch.setattr(recovery.campaign_policy_service, "require_policy",
                        lambda: {"_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        recovery.v7, "_target_rows", lambda *_a, **_k: (_signup_rows(), None))
    monkeypatch.setattr(
        recovery.v7, "_discount_scope", lambda *_a, **_k: (_discount_rows(), None))
    manifest, _, _ = _seed_v8_claimed_preupload_failure(
        db_session, monkeypatch)
    frozen_base = {key: value for key, value in manifest.items()
                   if key != "inspection_baseline"}
    monkeypatch.setattr(recovery, "_fixed_manifest", lambda *_a, **_k: frozen_base)
    monkeypatch.setattr(
        recovery, "_validate_claimed_preupload_after_nested_modal_attempt",
        lambda _attempt: (True, {}))
    inspect_scope = {"bound": "v16-nested-modal-fix"}

    def fake_v16(_db, *, payload, timeout_s=2400):
        assert payload["phase"] == "inspect"
        return {
            "ok": True, "platform_write": False, "claim_created": True,
            "resume_claim_sha256": recovery.CLAIMED_PREUPLOAD_V15_CLAIM_SHA256,
            "last_checkpoint": recovery.CLAIMED_PREUPLOAD_LAST_STEP,
            "inspect_scope": inspect_scope,
            "inspect_scope_sha256": recovery.v6._hash(inspect_scope),
            "reservation_token": "v16-reservation-token",
            "lease_expires_at_epoch": 4102444800.0,
            "web_agent_job_id": "job-v16-inspect",
        }

    captured = {}
    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v8_preupload_resume_v16",
        fake_v16)
    monkeypatch.setattr(
        recovery, "_commit_and_readback",
        lambda _db, **kwargs: captured.update(kwargs) or {"ok": True})
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v16",
        confirmation=recovery.CLAIMED_PREUPLOAD_NESTED_MODAL_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert result["ok"] is True, result
    assert captured["resume_claim_sha256"] == (
        recovery.CLAIMED_PREUPLOAD_V15_CLAIM_SHA256)
    assert captured["use_preupload_v16_endpoint"] is True


def test_v16_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v16",
        confirmation=recovery.CLAIMED_PREUPLOAD_NESTED_MODAL_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v16"


def test_v17_request_schema_accepts_only_exact_mode_and_confirmation():
    body = campaigns.CampaignPlan8FinalRecoveryV8In(
        workflow_key=recovery.WORKFLOW_KEY, plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v17",
        confirmation=recovery.CLAIMED_PREUPLOAD_MOBAN_TEXT_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert body.mode == "resume_claimed_preupload_v17"


def test_v17_mode_maps_only_to_moban_state(db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_v8_claimed_preupload_failure(db_session, monkeypatch)
    monkeypatch.setattr(recovery.v6, "_identity_allowed",
                        lambda _plan: (True, {}))
    captured = {}
    monkeypatch.setattr(
        recovery, "_resume_claimed_preupload",
        lambda _db, **kwargs: captured.update(kwargs) or {"ok": True})
    result = recovery.recover_plan8_final_v8(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=8,
        mode="resume_claimed_preupload_v17",
        confirmation=recovery.CLAIMED_PREUPLOAD_MOBAN_TEXT_CONFIRMATION,
        target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert result["ok"] is True
    assert captured["accept_moban_text_state"] is True
    assert captured["accept_nested_modal_state"] is False


def test_v8_commit_preserves_state_drift_diagnostics():
    manifest = recovery._fixed_manifest(
        _signup_rows(), _discount_rows(), recovery.EXPECTED_POLICY_SHA256)
    manifest = {**manifest, "inspection_baseline": {}}
    scope = recovery.v6._hash(manifest)
    ok, detail = recovery.validate_commit({
        "ok": False,
        "error": "plan8_v8_state_changed_before_claim",
        "different_fields": ["draft_record_before_hashes"],
        "detail": {"phase": "preclaim_compare"},
        "candidate_price_evidence": {"bound_error": "candidate unavailable"},
        "platform_write": False,
        "claim_created": False,
    }, manifest, scope)
    assert ok is False
    assert detail["different_fields"] == ["draft_record_before_hashes"]
    assert detail["web_agent_detail"] == {"phase": "preclaim_compare"}
    assert detail["candidate_price_evidence"] == {
        "bound_error": "candidate unavailable"}


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

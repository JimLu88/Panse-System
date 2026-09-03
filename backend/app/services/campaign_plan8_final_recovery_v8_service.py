"""Claim-bound Plan 8 continuation after V7 proved zero platform writes."""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_execution_service,
    campaign_plan8_final_recovery_v6_service as v6,
    campaign_plan8_final_recovery_v7_service as v7,
    campaign_policy_service,
    web_agent_service,
)


WORKFLOW_KEY = v7.WORKFLOW_KEY
PLAN_ID = v7.PLAN_ID
EXPECTED_STATUS = v7.EXPECTED_STATUS
RECOVERY_VERSION = 8
OPERATION = "plan8_final_recovery_v8"
EXECUTION_SOURCE = "campaign_super88_plan8_final_recovery_v8"
EXPECTED_POLICY_SHA256 = v7.EXPECTED_POLICY_SHA256
EXPECTED_TARGET_SCOPE_SHA256 = v7.EXPECTED_TARGET_SCOPE_SHA256
IDENTITY = v7.IDENTITY
TARGET_ITEM_IDS = v7.TARGET_ITEM_IDS
ADD_PAIRS = v7.ADD_PAIRS
OLD_DISCOUNT_ACTIVITY_ID = v7.OLD_DISCOUNT_ACTIVITY_ID
READBACK_PLAN_STATUSES = v7.READBACK_PLAN_STATUSES
V7_ATTEMPT_ID = "5a72360877df3c3fad221ee2"
EXPECTED_RESUME_EVIDENCE = {
    "v7_attempt_id": V7_ATTEMPT_ID,
    "v7_operation": "plan8_final_recovery_v7",
    "v7_state": "unknown_no_retry",
    "v7_last_checkpoint": "discount_terminal",
    "v7_claim_sha256": (
        "733d0fe55454ea7274ab5f61516fc3730a973e7ab961fdd22247f661321c23b2"
    ),
    "v7_import_ok": 0,
    "v7_import_failed": 8,
    "v7_import_submitted": False,
    "fresh_readback_new_discount_rows": 0,
    "fresh_readback_old_discount_rows": 53,
    "fresh_readback_old_discount_sha256": (
        "9c85a468a5fed6db667b7d388fea8e0ecb7148a2c9e70c8cd11e8cb06b52c2e2"
    ),
}
EXECUTION_ORDER = [
    "patch_6_bound_drafts_to_78_skus",
    "publish_6_bound_drafts",
    "supplement_8_discounts_in_existing_activity",
    "official_readback",
]
EXPECTED_COMMIT_CHECKPOINTS = [
    "claimed", "draft_patch_terminal", "draft_patch_readback_exact",
    "publish_terminal", "campaign_readback_exact", "discount_terminal",
    "discount_readback_exact", "official_readback_exact",
]
EXECUTE_CONFIRMATION = "EXECUTE_ONCE_PLAN8_V8_RESUME_V7_ZERO_WRITE"
READBACK_CONFIRMATION = "READBACK_ONLY_PLAN8_V8_NO_PLATFORM_WRITE"
PRECLAIM_RESUME_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_MANIFEST_PHASE_FIX_V3"
)
PRECLAIM_ATTEMPT_ID = "edaf6b609dad46fbab90c7e8"
PRECLAIM_SCOPE_SHA256 = (
    "08170d64d354c1e50f7f87270de118874b6e9db2513aae46cec94e9f5db8eb3a"
)
PRECLAIM_REQUEST_ID = "plan8-final-v8-67b588e1b5278ff4"
PRECLAIM_WEB_AGENT_JOB_ID = "job2"
PRECLAIM_LAST_STEP = "plan8_final_v8_commit"
PRECLAIM_ERROR_CODE = "ValueError: plan8_v6_manifest_fields_invalid"
CLAIMED_PREUPLOAD_RESUME_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_BATCH_DIALOG_FIX_V4"
)
CLAIMED_PREUPLOAD_POST_READBACK_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_ZERO_WRITE_READBACK_V5"
)
CLAIMED_PREUPLOAD_LEASE_SCOPE_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_LEASE_SCOPE_FIX_V6"
)
CLAIMED_PREUPLOAD_BUSY_WAIT_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_PREWRITE_BUSY_V7"
)
CLAIMED_PREUPLOAD_LEASE_EXPIRY_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_CLAIM_LEASE_EXPIRY_V8"
)
CLAIMED_PREUPLOAD_DIALOG_FIX_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_BATCH_IMPORT_DIALOG_FIX_V9"
)
CLAIMED_PREUPLOAD_CAMPAIGN_GUARD_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_CAMPAIGN_GUARD_SETTLE_FIX_V10"
)
CLAIMED_PREUPLOAD_CLAIM_VERIFY_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_V10_CLAIM_VERIFY_FIX_V11"
)
CLAIMED_PREUPLOAD_LAZY_IMPORT_CONFIRMATION = (
    "RESUME_ONCE_PLAN8_V8_AFTER_LAZY_IMPORT_BINDING_FIX_V12"
)
PREUPLOAD_BUSY_WAIT_SECONDS = 600.0
PREUPLOAD_BUSY_POLL_SECONDS = 5.0
CLAIMED_PREUPLOAD_SCOPE_SHA256 = (
    "04ea6c51d5bc50ca3c4361fd503ce75503772a2fc6a98cb254ec5842a511d6d3"
)
CLAIMED_PREUPLOAD_CLAIM_SHA256 = (
    "4b71a1d5337e0de45fc732ea1ee0007eb35ea9f49995a6cf2f3f59ab82c7a37f"
)
CLAIMED_PREUPLOAD_V9_CLAIM_SHA256 = (
    "3435ec34f23975771e57d2d7d6f17b2e6d9463dff8439b4eae7aedb35a46255c"
)
CLAIMED_PREUPLOAD_V11_CLAIM_SHA256 = (
    "b480484bbd52654bf29d4c78e6594d86f3b440587e54eca125d0f4232e63f72c"
)
CLAIMED_PREUPLOAD_LAST_STEP = "draft_patch_terminal"
CLAIMED_PREUPLOAD_ERROR_CODE = "plan8_v8_unknown_outcome_no_retry"
POST_READBACK_RESULT_SUMMARY_SHA256 = (
    "956dc7d744a45800924e93afa060042f98a95cda8a8b9f858b3ca403afeddcb4"
)
POST_READBACK_DETAIL_SHA256 = (
    "0bec6be3c5107d28ba79fc3736a85bea93f1ae339782ae3be73439e56d90ec1d"
)
POST_READBACK_MISSING_SKU_IDS = [
    "6234601898881", "6234601898883", "6234601898885",
    "6234601898887", "6287431318354", "6287431318356",
    "6287431318358", "6287431318360",
]
V5_RESULT_SUMMARY_SHA256 = (
    "8b6b5d5f5cbb2546fb4838c33757aa22685496104dd9a37d9469c85a48af2394"
)
V5_INSPECTION_SHA256 = (
    "1a49d4a3822c06d388d2e17d386aaf813bc15326badb0056c424b9f3e70b7ac6"
)
V5_COMMIT_SHA256 = (
    "a29c273e9f264ba207db1fabf4d04ae8a04eb5489bd2ffd81fc40050d9f00aa3"
)
V7_RESULT_SUMMARY_SHA256 = (
    "164ae521bfabde2f50b837b388c8f479670b75f23084c3b16015538512d91b6e"
)
V7_INSPECTION_SHA256 = (
    "7a865a89280df25b1d49e5d82d64d014a333d6b8cb673b430b3d29d298b8c2f1"
)
V7_COMMIT_SHA256 = (
    "08193800da6df7fbb25a7b57ef0fc69c19001c04d3f3b52921be18d24a7825dc"
)
V8_RESULT_SUMMARY_SHA256 = (
    "1056020857c41455ba1b40b3648a35ea220a199753ada573b43ca513f0e7fcf8"
)
V8_INSPECTION_SHA256 = (
    "a091bb015f4c57dd35303dd4eb6613405ddee894e119d36eb9f9c0769b792c44"
)
V8_COMMIT_SHA256 = (
    "a602f39ed942694b4106cccf680a9d41f3a96ae37f858facea78640d1d530eb8"
)
V9_RESULT_SUMMARY_SHA256 = (
    "8245b773c08f627b8eff3eb56a6d8581cd1e3d9e45bf8acadf4214de371da452"
)
V9_INSPECTION_SHA256 = (
    "e8c06efc6bce3ce9dd7fd65bc730b4bb6c17acd12a93493155d19f5c370b24ee"
)
V9_COMMIT_SHA256 = (
    "da096ffab840c1514aba5fe1ff65d701481c46e5b0696e6cc1ca4fe243386976"
)
V10_RESULT_SUMMARY_SHA256 = (
    "23294c83f5a806053bf02b722748d18113204006b61711dc03af4e475c0a8337"
)
V10_INSPECTION_SHA256 = (
    "425ed53a2d289e69e39738fd44d6e44b8dcaf24b85fd017e5d2cabd9536bf243"
)
V10_COMMIT_SHA256 = (
    "381fb1a1f5c43f895e6cacad89f3ae730b4435ac9b04fbb55a6c292bd48bc4e7"
)
V11_RESULT_SUMMARY_SHA256 = (
    "0fecab0b102c7749eefe9b60b7266d7328ec5fa24bc9ba38a94bb5235e8ac1b0"
)
V11_INSPECTION_SHA256 = (
    "2135130a81b43b1694f28201ee23e55b239e9eda4cf6a2a6dd4b08c0cdaa5bf3"
)
V11_COMMIT_SHA256 = (
    "a602f39ed942694b4106cccf680a9d41f3a96ae37f858facea78640d1d530eb8"
)


def _boundary(*, platform_write: bool = False) -> dict:
    return {**v7._boundary(platform_write=platform_write),
            "activity_create": False,
            "existing_activity_edit": platform_write,
            "v7_execute_retry": False}


def _fail(error: str, **detail) -> dict:
    return {"ok": False, "error": error, **detail,
            "execution_boundary": _boundary(platform_write=False)}


def _fixed_manifest(target_rows: list[dict], discount_rows: list[dict],
                    policy_sha: str) -> dict:
    manifest = v7._fixed_manifest(target_rows, discount_rows, policy_sha)
    manifest["recovery_version"] = RECOVERY_VERSION
    manifest.pop("recovery_evidence", None)
    manifest["resume_evidence"] = dict(EXPECTED_RESUME_EVIDENCE)
    manifest["execution_order"] = list(EXECUTION_ORDER)
    return manifest


def _attempts(db: Session) -> list[CampaignExecutionAttempt]:
    return list(db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
    )).scalars())


def _attempt_for_scope(db: Session,
                       scope_sha256: str) -> CampaignExecutionAttempt | None:
    return db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
        CampaignExecutionAttempt.scope_sha256 == scope_sha256,
    )).scalar_one_or_none()


def _validate_prerequisite(db: Session) -> tuple[bool, dict]:
    row = db.get(CampaignExecutionAttempt, V7_ATTEMPT_ID)
    manifest = ((row.result_summary or {}).get("manifest")
                if row is not None else None)
    detail = {
        "attempt_id": V7_ATTEMPT_ID,
        "operation": getattr(row, "operation", None),
        "state": getattr(row, "state", None),
        "write_claimed": getattr(row, "write_claimed", None),
        "platform_write_observed": getattr(
            row, "platform_write_observed", None),
        "last_step": getattr(row, "last_step", None),
        "manifest_scope_sha256": getattr(row, "scope_sha256", None),
    }
    ok = bool(
        row is not None
        and row.operation == "plan8_final_recovery_v7"
        and row.state == "unknown_no_retry"
        and row.write_claimed is True
        and row.platform_write_observed is not True
        and row.last_step in {"discount_terminal", "readback_not_complete"}
        and isinstance(manifest, dict)
        and v6._hash(manifest) == row.scope_sha256)
    return ok, detail


def validate_inspection(result: dict, manifest: dict,
                        manifest_sha256: str) -> tuple[bool, dict]:
    base_ok, detail = v7.validate_inspection(
        result, manifest, manifest_sha256)
    evidence = result.get("resume_evidence") or {}
    evidence_ok = bool(evidence.get("ok") is True and all(
        evidence.get(key) == value
        for key, value in EXPECTED_RESUME_EVIDENCE.items()))
    return bool(base_ok and evidence_ok), {
        **detail, "resume_evidence": evidence,
        "web_agent_error": result.get("error"),
        "web_agent_status": result.get("status"),
        "web_agent_step": result.get("step"),
        "web_agent_facts": result.get("facts"),
        "web_agent_claim_created": result.get("claim_created"),
        "web_agent_need_scan": result.get("need_scan"),
        "v8_claim_absent": result.get("v8_claim_absent"),
        "v8_claim_sha256": result.get("v8_claim_sha256"),
    }


def validate_commit(result: dict, manifest: dict,
                    manifest_sha256: str) -> tuple[bool, dict]:
    rewritten = {**result, "checkpoints": v6.EXPECTED_COMMIT_CHECKPOINTS}
    base_ok, detail = v6.validate_commit(
        rewritten, manifest, manifest_sha256)
    checkpoints_ok = result.get("checkpoints") == EXPECTED_COMMIT_CHECKPOINTS
    return bool(base_ok and checkpoints_ok), {
        **detail, "checkpoints": result.get("checkpoints"),
        "v8_checkpoint_order_ok": checkpoints_ok,
        "web_agent_error": result.get("error"),
        "web_agent_error_code": result.get("error_code"),
        "web_agent_status": result.get("status"),
        "last_checkpoint": result.get("last_checkpoint"),
        "claim_created": result.get("claim_created"),
        "different_fields": result.get("different_fields") or [],
        "web_agent_detail": result.get("detail"),
        "candidate_price_evidence": result.get("candidate_price_evidence"),
        "reservation_consumed": result.get("reservation_consumed"),
        "web_agent_job_id": result.get("web_agent_job_id"),
    }


def validate_readback(result: dict, manifest: dict,
                      manifest_sha256: str) -> tuple[bool, dict]:
    return v6.validate_readback(result, manifest, manifest_sha256)


def verify_plan8_final_v8_claim(
        db: Session, *, attempt_id: str, workflow_key: str, plan_id: int,
        operation: str, scope_sha256: str, inspect_scope_sha256: str,
        reservation_token_sha256: str) -> dict:
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    manifest = ((attempt.result_summary or {}).get("manifest")
                if attempt is not None else None)
    baseline = ((manifest or {}).get("inspection_baseline")
                if isinstance(manifest, dict) else None)
    try:
        expires = float((baseline or {}).get(
            "reservation_expires_at_epoch") or 0)
    except (TypeError, ValueError):
        expires = 0
    verified = bool(
        attempt is not None and workflow_key == WORKFLOW_KEY
        and plan_id == PLAN_ID and operation == OPERATION
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION and attempt.scope_sha256 == scope_sha256
        and attempt.state == "write_claimed" and attempt.write_claimed is True
        and attempt.write_claimed_at is not None and bool(attempt.request_id)
        and isinstance(manifest, dict) and v6._hash(manifest) == scope_sha256
        and manifest.get("resume_evidence") == EXPECTED_RESUME_EVIDENCE
        and isinstance(baseline, dict)
        and baseline.get("inspect_scope_sha256") == inspect_scope_sha256
        and baseline.get("reservation_token_sha256")
        == reservation_token_sha256
        and expires > datetime.now(timezone.utc).timestamp())
    return {
        "ok": verified, "verified": verified, "attempt_id": attempt_id,
        "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
        "operation": OPERATION, "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", False),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "inspect_scope_sha256": (baseline.get("inspect_scope_sha256")
                                 if isinstance(baseline, dict) else None),
        "reservation_token_sha256": (
            baseline.get("reservation_token_sha256")
            if isinstance(baseline, dict) else None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "execution_boundary": {**_boundary(platform_write=False),
                               "platform_read": False},
    }


def verify_plan8_final_v8_preupload_claim(
        db: Session, *, attempt_id: str, workflow_key: str, plan_id: int,
        operation: str, scope_sha256: str, inspect_scope_sha256: str,
        reservation_token_sha256: str, resume_claim_sha256: str) -> dict:
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    resume = summary.get("claimed_preupload_resume") or {}
    try:
        expires = float(resume.get("reservation_expires_at_epoch") or 0)
    except (TypeError, ValueError):
        expires = 0
    v9_claim_steps = {
        "platform_write_claim_claimed_preupload_resume_v9",
        "platform_write_claim_claimed_preupload_resume_v10",
        "platform_write_claim_claimed_preupload_resume_v11",
    }
    step = getattr(attempt, "last_step", None)
    expected_resume_claim_sha256 = (
        CLAIMED_PREUPLOAD_V11_CLAIM_SHA256
        if step == "platform_write_claim_claimed_preupload_resume_v12"
        else (CLAIMED_PREUPLOAD_V9_CLAIM_SHA256
              if step in v9_claim_steps
              else CLAIMED_PREUPLOAD_CLAIM_SHA256))
    verified = bool(
        attempt is not None and workflow_key == WORKFLOW_KEY
        and plan_id == PLAN_ID and operation == OPERATION
        and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == scope_sha256
        == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "write_claimed" and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and isinstance(manifest, dict) and v6._hash(manifest) == scope_sha256
        and resume == {
            "source_claim_sha256": expected_resume_claim_sha256,
            "inspect_scope_sha256": inspect_scope_sha256,
            "reservation_token_sha256": reservation_token_sha256,
            "reservation_expires_at_epoch": expires,
        }
        and resume_claim_sha256 == expected_resume_claim_sha256
        and expires > datetime.now(timezone.utc).timestamp())
    return {
        "ok": verified, "verified": verified, "attempt_id": attempt_id,
        "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
        "operation": OPERATION, "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", False),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "inspect_scope_sha256": inspect_scope_sha256,
        "reservation_token_sha256": reservation_token_sha256,
        "resume_claim_sha256": resume_claim_sha256,
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "execution_boundary": {**_boundary(platform_write=False),
                               "platform_read": False},
    }


def _readback_existing(db: Session, plan: CampaignPlan,
                       attempt: CampaignExecutionAttempt) -> dict:
    if str(plan.status or "") not in READBACK_PLAN_STATUSES:
        return _fail("plan8_final_v8_readback_plan_status_not_allowed",
                     actual_status=plan.status, attempt_id=attempt.id)
    manifest = (attempt.result_summary or {}).get("manifest")
    if not isinstance(manifest, dict):
        return _fail("plan8_final_v8_attempt_manifest_missing",
                     attempt_id=attempt.id)
    if v6._hash(manifest) != attempt.scope_sha256:
        return _fail("plan8_final_v8_attempt_scope_mismatch",
                     attempt_id=attempt.id)
    try:
        result = web_agent_service.recover_plan8_final_v8(
            db, payload={"phase": "readback",
                         "scope_sha256": attempt.scope_sha256,
                         "manifest": manifest, "attempt_id": attempt.id})
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": type(exc).__name__,
                  "platform_write": False}
    ok, detail = validate_readback(result, manifest, attempt.scope_sha256)
    if not ok:
        prior = dict(attempt.result_summary or {})
        prior["last_readback"] = detail
        attempt.result_summary = prior
        attempt.last_step = "readback_not_complete"
        attempt.error_code = "post_submit_readback_not_complete"
        attempt.web_agent_job_id = str(
            result.get("web_agent_job_id") or "")[:64] or attempt.web_agent_job_id
        db.commit()
        return _fail("plan8_final_v8_readback_not_complete",
                     attempt_id=attempt.id, readback=detail,
                     need_scan=bool(result.get("need_scan")))
    campaign_execution_service.record_platform_terminal(
        db, attempt, state="completed",
        platform_write_observed=attempt.platform_write_observed,
        step="readback_verified", job_id=detail.get("web_agent_job_id"),
        result_summary={**dict(attempt.result_summary or {}),
                        "manifest": manifest, "readback": detail})
    plan.status = "reconciled"
    db.commit()
    return {"ok": True, "readback_only": True, "attempt_id": attempt.id,
            "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
            "plan_status": plan.status, "verification": detail,
            "execution_boundary": _boundary(platform_write=False)}


def _validate_preclaim_resume_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    manifest = ((attempt.result_summary or {}).get("manifest")
                if attempt is not None else None)
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
    }
    ok = bool(
        attempt is not None
        and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID
        and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == PRECLAIM_SCOPE_SHA256
        and attempt.state == "unknown_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is None
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == PRECLAIM_LAST_STEP
        and attempt.error_code == PRECLAIM_ERROR_CODE
        and attempt.web_agent_job_id == PRECLAIM_WEB_AGENT_JOB_ID
        and isinstance(manifest, dict)
        and v6._hash(manifest) == PRECLAIM_SCOPE_SHA256
    )
    return ok, detail


def _validate_claimed_preupload_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    commit = summary.get("commit") or {}
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
        "commit": commit,
    }
    ok = bool(
        attempt is not None and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "failed_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == CLAIMED_PREUPLOAD_LAST_STEP
        and attempt.error_code == CLAIMED_PREUPLOAD_ERROR_CODE
        and attempt.web_agent_job_id == "job2"
        and isinstance(manifest, dict)
        and v6._hash(manifest) == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and commit.get("platform_write") is False
        and commit.get("reservation_consumed") is True
        and commit.get("claim_created") is True
        and commit.get("last_checkpoint") == CLAIMED_PREUPLOAD_LAST_STEP
        and commit.get("web_agent_error") == CLAIMED_PREUPLOAD_ERROR_CODE
        and not commit.get("patched_record_ids")
        and not commit.get("published_record_ids")
        and not commit.get("discount_pairs_written"))
    return ok, detail


def _validate_claimed_preupload_after_readback_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    commit = summary.get("commit") or {}
    readback = summary.get("last_readback") or {}
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
        "result_summary_sha256": v6._hash(summary),
        "last_readback_sha256": v6._hash(readback),
        "commit": commit,
    }
    ok = bool(
        attempt is not None and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "failed_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == "readback_not_complete"
        and attempt.error_code == "post_submit_readback_not_complete"
        and attempt.web_agent_job_id == "job3"
        and isinstance(manifest, dict)
        and v6._hash(manifest) == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and v6._hash(summary) == POST_READBACK_RESULT_SUMMARY_SHA256
        and v6._hash(readback) == POST_READBACK_DETAIL_SHA256
        and readback.get("record_count") == 6
        and readback.get("sku_count") == 70
        and readback.get("custom_sku_count") == 18
        and readback.get("missing_sku_ids") == POST_READBACK_MISSING_SKU_IDS
        and readback.get("unexpected_sku_ids") == []
        and readback.get("discount_rows") == []
        and readback.get("web_agent_job_id") == "job3"
        and commit.get("platform_write") is False
        and commit.get("reservation_consumed") is True
        and commit.get("claim_created") is True
        and commit.get("last_checkpoint") == CLAIMED_PREUPLOAD_LAST_STEP
        and commit.get("web_agent_error") == CLAIMED_PREUPLOAD_ERROR_CODE
        and not commit.get("patched_record_ids")
        and not commit.get("published_record_ids")
        and not commit.get("discount_pairs_written"))
    return ok, detail


def _validate_claimed_preupload_after_lease_scope_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    """Accept only the exact V5 no-write stop caused by lease-token drift."""
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    inspection = summary.get("inspection") or {}
    commit = summary.get("commit") or {}
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
        "result_summary_sha256": v6._hash(summary),
        "inspection_sha256": v6._hash(inspection),
        "commit_sha256": v6._hash(commit),
        "commit": commit,
    }
    ok = bool(
        attempt is not None and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "failed_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == "plan8_final_v8_commit"
        and attempt.error_code == "plan8_v8_state_changed_before_claim"
        and attempt.web_agent_job_id == "job2"
        and isinstance(manifest, dict)
        and v6._hash(manifest) == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and v6._hash(summary) == V5_RESULT_SUMMARY_SHA256
        and v6._hash(inspection) == V5_INSPECTION_SHA256
        and v6._hash(commit) == V5_COMMIT_SHA256
        and commit.get("platform_write") is False
        and commit.get("claim_created") is False
        and commit.get("web_agent_error")
        == "plan8_v8_state_changed_before_claim"
        and not commit.get("patched_record_ids")
        and not commit.get("published_record_ids")
        and not commit.get("discount_pairs_written"))
    return ok, detail


def _validate_claimed_preupload_after_lease_expiry_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    """Accept only the exact V7 no-write stop after its lease expired."""
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    inspection = summary.get("inspection") or {}
    commit = summary.get("commit") or {}
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
        "result_summary_sha256": v6._hash(summary),
        "inspection_sha256": v6._hash(inspection),
        "commit_sha256": v6._hash(commit),
        "commit": commit,
    }
    ok = bool(
        attempt is not None and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "failed_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == "plan8_final_v8_commit"
        and attempt.error_code == "plan8_v8_erp_claim_not_verified"
        and attempt.web_agent_job_id == "job2"
        and isinstance(manifest, dict)
        and v6._hash(manifest) == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and v6._hash(summary) == V7_RESULT_SUMMARY_SHA256
        and v6._hash(inspection) == V7_INSPECTION_SHA256
        and v6._hash(commit) == V7_COMMIT_SHA256
        and inspection.get("resume_claim_sha256")
        == CLAIMED_PREUPLOAD_CLAIM_SHA256
        and commit.get("platform_write") is False
        and commit.get("claim_created") is False
        and commit.get("web_agent_error") == "plan8_v8_erp_claim_not_verified"
        and (commit.get("web_agent_detail") or {}).get("error")
        == "erp_preupload_claim_verify_unavailable"
        and not commit.get("patched_record_ids")
        and not commit.get("published_record_ids")
        and not commit.get("discount_pairs_written"))
    return ok, detail


def _validate_claimed_preupload_after_dialog_mismatch_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    """Accept only V8's frozen no-write batch-import-dialog mismatch."""
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    inspection = summary.get("inspection") or {}
    commit = summary.get("commit") or {}
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
        "result_summary_sha256": v6._hash(summary),
        "inspection_sha256": v6._hash(inspection),
        "commit_sha256": v6._hash(commit),
        "commit": commit,
    }
    ok = bool(
        attempt is not None and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "failed_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == "draft_patch_terminal"
        and attempt.error_code == "plan8_v8_unknown_outcome_no_retry"
        and attempt.web_agent_job_id == "job2"
        and isinstance(manifest, dict)
        and v6._hash(manifest) == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and v6._hash(summary) == V8_RESULT_SUMMARY_SHA256
        and v6._hash(inspection) == V8_INSPECTION_SHA256
        and v6._hash(commit) == V8_COMMIT_SHA256
        and inspection.get("resume_claim_sha256")
        == CLAIMED_PREUPLOAD_CLAIM_SHA256
        and commit.get("platform_write") is False
        and commit.get("reservation_consumed") is True
        and commit.get("claim_created") is True
        and commit.get("last_checkpoint") == "draft_patch_terminal"
        and commit.get("web_agent_error")
        == "plan8_v8_unknown_outcome_no_retry"
        and not commit.get("patched_record_ids")
        and not commit.get("published_record_ids")
        and not commit.get("discount_pairs_written"))
    return ok, detail


def _validate_claimed_preupload_after_campaign_guard_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    """Accept only V9's frozen pre-claim read-only campaign-shell stop."""
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    inspection = summary.get("inspection") or {}
    commit = summary.get("commit") or {}
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
        "result_summary_sha256": v6._hash(summary),
        "inspection_sha256": v6._hash(inspection),
        "commit_sha256": v6._hash(commit),
        "commit": commit,
    }
    ok = bool(
        attempt is not None and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "unknown_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is None
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == "plan8_final_v8_commit"
        and attempt.error_code == "plan8_v6_bound_draft_campaign_guard_failed"
        and attempt.web_agent_job_id == "job2"
        and isinstance(manifest, dict)
        and v6._hash(manifest) == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and v6._hash(summary) == V9_RESULT_SUMMARY_SHA256
        and v6._hash(inspection) == V9_INSPECTION_SHA256
        and v6._hash(commit) == V9_COMMIT_SHA256
        and inspection.get("resume_claim_sha256")
        == CLAIMED_PREUPLOAD_V9_CLAIM_SHA256
        and commit.get("platform_write") is None
        and commit.get("claim_created") is False
        and commit.get("web_agent_error")
        == "plan8_v6_bound_draft_campaign_guard_failed"
        and not commit.get("patched_record_ids")
        and not commit.get("published_record_ids")
        and not commit.get("discount_pairs_written"))
    return ok, detail


def _validate_claimed_preupload_after_claim_verify_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    """Accept only V10's frozen zero-write claim-verifier rejection."""
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    inspection = summary.get("inspection") or {}
    commit = summary.get("commit") or {}
    web_detail = commit.get("web_agent_detail") or {}
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
        "result_summary_sha256": v6._hash(summary),
        "inspection_sha256": v6._hash(inspection),
        "commit_sha256": v6._hash(commit),
        "commit": commit,
    }
    ok = bool(
        attempt is not None and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "failed_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == "plan8_final_v8_commit"
        and attempt.error_code == "plan8_v8_erp_claim_not_verified"
        and attempt.web_agent_job_id == "job2"
        and isinstance(manifest, dict)
        and v6._hash(manifest) == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and v6._hash(summary) == V10_RESULT_SUMMARY_SHA256
        and v6._hash(inspection) == V10_INSPECTION_SHA256
        and v6._hash(commit) == V10_COMMIT_SHA256
        and inspection.get("resume_claim_sha256")
        == CLAIMED_PREUPLOAD_V9_CLAIM_SHA256
        and commit.get("platform_write") is False
        and commit.get("claim_created") is False
        and commit.get("web_agent_error") == "plan8_v8_erp_claim_not_verified"
        and web_detail.get("error") == "erp_preupload_claim_verify_rejected"
        and web_detail.get("http_status") == 409
        and not commit.get("patched_record_ids")
        and not commit.get("published_record_ids")
        and not commit.get("discount_pairs_written"))
    return ok, detail


def _validate_claimed_preupload_after_lazy_import_attempt(
        attempt: CampaignExecutionAttempt | None) -> tuple[bool, dict]:
    """Accept only V11's frozen no-upload lazy-file-input stop."""
    summary = dict(getattr(attempt, "result_summary", None) or {})
    manifest = summary.get("manifest")
    inspection = summary.get("inspection") or {}
    commit = summary.get("commit") or {}
    detail = {
        "attempt_id": getattr(attempt, "id", None),
        "scope_sha256": getattr(attempt, "scope_sha256", None),
        "state": getattr(attempt, "state", None),
        "write_claimed": getattr(attempt, "write_claimed", None),
        "platform_write_observed": getattr(
            attempt, "platform_write_observed", None),
        "automatic_retry_allowed": getattr(
            attempt, "automatic_retry_allowed", None),
        "request_id": getattr(attempt, "request_id", None),
        "last_step": getattr(attempt, "last_step", None),
        "error_code": getattr(attempt, "error_code", None),
        "web_agent_job_id": getattr(attempt, "web_agent_job_id", None),
        "result_summary_sha256": v6._hash(summary),
        "inspection_sha256": v6._hash(inspection),
        "commit_sha256": v6._hash(commit),
        "commit": commit,
    }
    ok = bool(
        attempt is not None and attempt.id == PRECLAIM_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and attempt.state == "failed_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and attempt.request_id == PRECLAIM_REQUEST_ID
        and attempt.last_step == "draft_patch_terminal"
        and attempt.error_code == "plan8_v8_unknown_outcome_no_retry"
        and attempt.web_agent_job_id == "job2"
        and isinstance(manifest, dict)
        and v6._hash(manifest) == CLAIMED_PREUPLOAD_SCOPE_SHA256
        and v6._hash(summary) == V11_RESULT_SUMMARY_SHA256
        and v6._hash(inspection) == V11_INSPECTION_SHA256
        and v6._hash(commit) == V11_COMMIT_SHA256
        and inspection.get("resume_claim_sha256")
        == CLAIMED_PREUPLOAD_V9_CLAIM_SHA256
        and commit.get("platform_write") is False
        and commit.get("reservation_consumed") is True
        and commit.get("claim_created") is True
        and commit.get("last_checkpoint") == "draft_patch_terminal"
        and commit.get("web_agent_error")
        == "plan8_v8_unknown_outcome_no_retry"
        and not commit.get("patched_record_ids")
        and not commit.get("published_record_ids")
        and not commit.get("discount_pairs_written"))
    return ok, detail


def _commit_and_readback(
        db: Session, *, plan: CampaignPlan,
        attempt: CampaignExecutionAttempt, manifest: dict,
        manifest_sha: str, inspect_scope_sha: str,
        reservation_token: str, inspection_detail: dict,
        commit_phase: str = "commit", resume_claim_sha256: str = "",
        use_preupload_v9_endpoint: bool = False,
        use_preupload_v10_endpoint: bool = False,
        use_preupload_v12_endpoint: bool = False) -> dict:
    claim_verification = {
        "attempt_id": attempt.id, "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID, "operation": OPERATION,
        "scope_sha256": manifest_sha,
        "inspect_scope_sha256": inspect_scope_sha,
        "reservation_token_sha256": inspection_detail[
            "reservation_token_sha256"],
    }
    if commit_phase == "resume_preupload_commit":
        claim_verification["resume_claim_sha256"] = resume_claim_sha256
    try:
        call = (
            web_agent_service.recover_plan8_final_v8_preupload_resume_v12
            if (commit_phase == "resume_preupload_commit"
                and use_preupload_v12_endpoint)
            else (web_agent_service.recover_plan8_final_v8_preupload_resume_v10
            if (commit_phase == "resume_preupload_commit"
                and use_preupload_v10_endpoint)
            else (web_agent_service.recover_plan8_final_v8_preupload_resume_v9
            if (commit_phase == "resume_preupload_commit"
                and use_preupload_v9_endpoint)
            else (web_agent_service.recover_plan8_final_v8_preupload_resume
                  if commit_phase == "resume_preupload_commit"
                  else web_agent_service.recover_plan8_final_v8))))
        committed = call(
            db, payload={"phase": ("commit" if commit_phase
                                    == "resume_preupload_commit"
                                    else commit_phase),
                         "scope_sha256": manifest_sha,
                         "inspect_scope_sha256": inspect_scope_sha,
                         "manifest": manifest, "attempt_id": attempt.id,
                         "reservation_token": reservation_token,
                         "claim_verification": claim_verification})
    except Exception as exc:  # noqa: BLE001
        committed = {"ok": False, "error": type(exc).__name__,
                     "platform_write": None}
    commit_ok, commit_detail = validate_commit(
        committed, manifest, manifest_sha)
    if not commit_ok:
        plan.status = "alarmed"
        db.commit()
        campaign_execution_service.record_platform_terminal(
            db, attempt,
            state="unknown_no_retry" if committed.get("platform_write") is None
            else "failed_no_retry",
            platform_write_observed=committed.get("platform_write"),
            step=str(committed.get("last_checkpoint")
                     or committed.get("step") or "plan8_final_v8_commit"),
            error_code=str(committed.get("error") or "commit_failed"),
            job_id=str(committed.get("web_agent_job_id") or "") or None,
            result_summary={**dict(attempt.result_summary or {}),
                            "manifest": manifest,
                            "inspection": inspection_detail,
                            "commit": commit_detail})
        return _fail("plan8_final_v8_commit_failed_no_retry",
                     attempt_id=attempt.id, commit=commit_detail)
    try:
        readback = web_agent_service.recover_plan8_final_v8(
            db, payload={"phase": "readback", "scope_sha256": manifest_sha,
                         "manifest": manifest, "attempt_id": attempt.id})
    except Exception as exc:  # noqa: BLE001
        readback = {"ok": False, "error": type(exc).__name__,
                    "platform_write": False}
    readback_ok, readback_detail = validate_readback(
        readback, manifest, manifest_sha)
    if not readback_ok:
        plan.status = "alarmed"
        db.commit()
        campaign_execution_service.record_platform_terminal(
            db, attempt, state="failed_no_retry", platform_write_observed=True,
            step="plan8_final_v8_readback",
            error_code="post_submit_readback_not_complete",
            job_id=str(readback.get("web_agent_job_id") or "") or None,
            result_summary={"manifest": manifest,
                            "inspection": inspection_detail,
                            "commit": committed,
                            "readback": readback_detail})
        return _fail("plan8_final_v8_readback_not_complete",
                     attempt_id=attempt.id, readback=readback_detail)
    campaign_execution_service.record_platform_terminal(
        db, attempt, state="completed", platform_write_observed=True,
        step="readback_verified",
        job_id=str(readback.get("web_agent_job_id") or "") or None,
        result_summary={"manifest": manifest,
                        "inspection": inspection_detail,
                        "commit": commit_detail,
                        "readback": readback_detail,
                        "finished_at": datetime.now(timezone.utc).isoformat()})
    plan.status = "reconciled"
    db.commit()
    return {"ok": True, "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
            "plan_status": plan.status, "attempt_id": attempt.id,
            "scope_sha256": manifest_sha, "verification": readback_detail,
            "execution_boundary": _boundary(platform_write=True)}


def _resume_claimed_preupload(
        db: Session, *, plan: CampaignPlan,
        attempt: CampaignExecutionAttempt,
        accept_post_readback_state: bool = False,
        accept_lease_scope_state: bool = False,
        accept_lease_expiry_state: bool = False,
        accept_dialog_mismatch_state: bool = False,
        accept_campaign_guard_state: bool = False,
        accept_claim_verify_state: bool = False,
        accept_lazy_import_state: bool = False,
        wait_prewrite_busy: bool = False) -> dict:
    validator = (
        _validate_claimed_preupload_after_lazy_import_attempt
        if accept_lazy_import_state
        else (_validate_claimed_preupload_after_claim_verify_attempt
        if accept_claim_verify_state
        else (_validate_claimed_preupload_after_campaign_guard_attempt
        if accept_campaign_guard_state
        else (_validate_claimed_preupload_after_dialog_mismatch_attempt
        if accept_dialog_mismatch_state
        else (_validate_claimed_preupload_after_lease_expiry_attempt
        if accept_lease_expiry_state
        else (_validate_claimed_preupload_after_lease_scope_attempt
              if accept_lease_scope_state
              else (_validate_claimed_preupload_after_readback_attempt
                    if accept_post_readback_state
                    else _validate_claimed_preupload_attempt)))))))
    resume_claim_sha256 = (
        CLAIMED_PREUPLOAD_V11_CLAIM_SHA256
        if accept_lazy_import_state
        else (CLAIMED_PREUPLOAD_V9_CLAIM_SHA256
        if (accept_dialog_mismatch_state or accept_campaign_guard_state
            or accept_claim_verify_state)
        else CLAIMED_PREUPLOAD_CLAIM_SHA256))
    preupload_web_call = (
        web_agent_service.recover_plan8_final_v8_preupload_resume_v12
        if accept_lazy_import_state
        else (web_agent_service.recover_plan8_final_v8_preupload_resume_v10
        if accept_campaign_guard_state or accept_claim_verify_state
        else (web_agent_service.recover_plan8_final_v8_preupload_resume_v9
        if accept_dialog_mismatch_state
        else web_agent_service.recover_plan8_final_v8_preupload_resume)))
    resume_ok, resume_detail = validator(attempt)
    if not resume_ok:
        return _fail("plan8_final_v8_claimed_preupload_attempt_mismatch",
                     attempt=resume_detail)
    manifest = dict((attempt.result_summary or {})["manifest"])
    baseline = manifest.get("inspection_baseline") or {}
    policy_sha = str(campaign_policy_service.require_policy().get("_sha256") or "")
    identity_ok, identity = v6._identity_allowed(plan)
    target_rows, scope_error = v7._target_rows(db, plan, identity, policy_sha)
    discount_rows, discount_error = v7._discount_scope(db, plan)
    current_base = (_fixed_manifest(target_rows, discount_rows, policy_sha)
                    if not scope_error and not discount_error else None)
    if (not identity_ok or policy_sha != EXPECTED_POLICY_SHA256
            or scope_error or discount_error or not isinstance(current_base, dict)
            or v6._hash(current_base) != baseline.get("inspect_scope_sha256")):
        return _fail("plan8_final_v8_claimed_preupload_scope_changed",
                     identity=identity, policy_sha256=policy_sha,
                     signup_scope_error=scope_error,
                     discount_scope_error=discount_error)
    db.commit()

    busy_observations = 0
    busy_wait_started = time.monotonic()
    while True:
        inspection = preupload_web_call(
            db, payload={"phase": "inspect",
                         "scope_sha256": CLAIMED_PREUPLOAD_SCOPE_SHA256,
                         "manifest": manifest, "attempt_id": attempt.id})
        exact_retryable_busy = bool(
            inspection.get("ok") is False
            and inspection.get("error") == "taobao_profile_busy"
            and inspection.get("step") == "preupload_resume_busy"
            and inspection.get("busy") is True
            and inspection.get("pre_write_busy") is True
            and inspection.get("retry_safe") is True
            and inspection.get("platform_write") is False)
        elapsed = time.monotonic() - busy_wait_started
        if (not wait_prewrite_busy or not exact_retryable_busy
                or elapsed >= PREUPLOAD_BUSY_WAIT_SECONDS):
            break
        busy_observations += 1
        time.sleep(min(PREUPLOAD_BUSY_POLL_SECONDS,
                       PREUPLOAD_BUSY_WAIT_SECONDS - elapsed))
    if busy_observations:
        inspection["prewrite_busy_wait"] = {
            "observations": busy_observations,
            "waited_seconds": round(time.monotonic() - busy_wait_started, 3),
            "bounded": True,
        }
    inspect_scope = inspection.get("inspect_scope")
    reservation_token = str(inspection.get("reservation_token") or "")
    try:
        lease_expires = float(inspection.get("lease_expires_at_epoch") or 0)
    except (TypeError, ValueError):
        lease_expires = 0
    inspect_scope_sha = (v6._hash(inspect_scope)
                         if isinstance(inspect_scope, dict) else "")
    inspection_ok = bool(
        inspection.get("ok") is True
        and inspection.get("platform_write") is False
        and inspection.get("claim_created") is True
        and inspection.get("resume_claim_sha256")
        == resume_claim_sha256
        and inspection.get("last_checkpoint") == CLAIMED_PREUPLOAD_LAST_STEP
        and inspection.get("inspect_scope_sha256") == inspect_scope_sha
        and isinstance(inspect_scope, dict) and reservation_token
        and lease_expires > datetime.now(timezone.utc).timestamp())
    if not inspection_ok:
        return _fail("plan8_final_v8_claimed_preupload_inspection_blocked",
                     inspection={key: value for key, value in inspection.items()
                                 if key != "reservation_token"})

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    attempt = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == PRECLAIM_ATTEMPT_ID,
    ).with_for_update()).scalar_one_or_none()
    resume_ok, resume_detail = validator(attempt)
    if (plan is None or plan.status != EXPECTED_STATUS or not resume_ok):
        return _fail("plan8_final_v8_claimed_preupload_state_changed",
                     plan_status=getattr(plan, "status", None),
                     attempt=resume_detail)
    summary = dict(attempt.result_summary or {})
    summary["claimed_preupload_resume"] = {
        "source_claim_sha256": resume_claim_sha256,
        "inspect_scope_sha256": inspect_scope_sha,
        "reservation_token_sha256": v6._hash(reservation_token),
        "reservation_expires_at_epoch": lease_expires,
    }
    attempt.state = "write_claimed"
    attempt.write_claimed = True
    attempt.write_claimed_at = datetime.now(timezone.utc)
    attempt.platform_write_observed = False
    attempt.automatic_retry_allowed = False
    attempt.last_step = (
        "platform_write_claim_claimed_preupload_resume_v12"
        if accept_lazy_import_state
        else ("platform_write_claim_claimed_preupload_resume_v11"
        if accept_claim_verify_state
        else ("platform_write_claim_claimed_preupload_resume_v10"
        if accept_campaign_guard_state
        else ("platform_write_claim_claimed_preupload_resume_v9"
        if accept_dialog_mismatch_state
        else ("platform_write_claim_claimed_preupload_resume_v8"
        if accept_lease_expiry_state
        else ("platform_write_claim_claimed_preupload_resume_v7"
              if wait_prewrite_busy
              else ("platform_write_claim_claimed_preupload_resume_v6"
                    if accept_lease_scope_state
                    else ("platform_write_claim_claimed_preupload_resume_v5"
                          if accept_post_readback_state
                          else "platform_write_claim_claimed_preupload_resume_v4"))))))))
    attempt.error_code = None
    attempt.web_agent_job_id = None
    attempt.result_summary = summary
    plan.status = "resume_executing"
    db.commit()
    inspection_detail = {
        "resume_claim_sha256": resume_claim_sha256,
        "inspect_scope_sha256": inspect_scope_sha,
        "reservation_token_sha256": v6._hash(reservation_token),
        "lease_expires_at_epoch": lease_expires,
        "web_agent_job_id": inspection.get("web_agent_job_id"),
    }
    return _commit_and_readback(
        db, plan=plan, attempt=attempt, manifest=manifest,
        manifest_sha=CLAIMED_PREUPLOAD_SCOPE_SHA256,
        inspect_scope_sha=inspect_scope_sha,
        reservation_token=reservation_token,
        inspection_detail=inspection_detail,
        commit_phase="resume_preupload_commit",
        resume_claim_sha256=resume_claim_sha256,
        use_preupload_v9_endpoint=accept_dialog_mismatch_state,
        use_preupload_v10_endpoint=(
            accept_campaign_guard_state or accept_claim_verify_state),
        use_preupload_v12_endpoint=accept_lazy_import_state)


def recover_plan8_final_v8(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, recovery_version: int,
        mode: str = "execute", confirmation: str = "",
        target_scope_sha256: str = "") -> dict:
    confirmations = {
        "execute": EXECUTE_CONFIRMATION,
        "readback": READBACK_CONFIRMATION,
        "resume_preclaim_v3": PRECLAIM_RESUME_CONFIRMATION,
        "resume_claimed_preupload_v4": CLAIMED_PREUPLOAD_RESUME_CONFIRMATION,
        "resume_claimed_preupload_v5": (
            CLAIMED_PREUPLOAD_POST_READBACK_CONFIRMATION),
        "resume_claimed_preupload_v6": (
            CLAIMED_PREUPLOAD_LEASE_SCOPE_CONFIRMATION),
        "resume_claimed_preupload_v7": (
            CLAIMED_PREUPLOAD_BUSY_WAIT_CONFIRMATION),
        "resume_claimed_preupload_v8": (
            CLAIMED_PREUPLOAD_LEASE_EXPIRY_CONFIRMATION),
        "resume_claimed_preupload_v9": (
            CLAIMED_PREUPLOAD_DIALOG_FIX_CONFIRMATION),
        "resume_claimed_preupload_v10": (
            CLAIMED_PREUPLOAD_CAMPAIGN_GUARD_CONFIRMATION),
        "resume_claimed_preupload_v11": (
            CLAIMED_PREUPLOAD_CLAIM_VERIFY_CONFIRMATION),
        "resume_claimed_preupload_v12": (
            CLAIMED_PREUPLOAD_LAZY_IMPORT_CONFIRMATION),
    }
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_status != EXPECTED_STATUS
            or recovery_version != RECOVERY_VERSION
            or mode not in confirmations
            or confirmation != confirmations.get(mode)
            or target_scope_sha256 != EXPECTED_TARGET_SCOPE_SHA256):
        return _fail("plan8_final_v8_request_not_allowed")
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    identity_ok, identity = v6._identity_allowed(plan)
    if not identity_ok:
        return _fail("plan8_final_v8_identity_not_allowed", identity=identity)
    attempts = _attempts(db)
    if mode in {"resume_claimed_preupload_v4",
                "resume_claimed_preupload_v5",
                "resume_claimed_preupload_v6",
                "resume_claimed_preupload_v7",
                "resume_claimed_preupload_v8",
                "resume_claimed_preupload_v9",
                "resume_claimed_preupload_v10",
                "resume_claimed_preupload_v11",
                "resume_claimed_preupload_v12"}:
        if len(attempts) != 1:
            return _fail("plan8_final_v8_claimed_preupload_attempt_ambiguous",
                         attempt_count=len(attempts))
        return _resume_claimed_preupload(
            db, plan=plan, attempt=attempts[0],
            accept_post_readback_state=(
                mode == "resume_claimed_preupload_v5"),
            accept_lease_scope_state=(
                mode in {"resume_claimed_preupload_v6",
                         "resume_claimed_preupload_v7"}),
            accept_lease_expiry_state=(
                mode == "resume_claimed_preupload_v8"),
            accept_dialog_mismatch_state=(
                mode == "resume_claimed_preupload_v9"),
            accept_campaign_guard_state=(
                mode == "resume_claimed_preupload_v10"),
            accept_claim_verify_state=(
                mode == "resume_claimed_preupload_v11"),
            accept_lazy_import_state=(
                mode == "resume_claimed_preupload_v12"),
            wait_prewrite_busy=(
                mode in {"resume_claimed_preupload_v7",
                         "resume_claimed_preupload_v8",
                         "resume_claimed_preupload_v9",
                         "resume_claimed_preupload_v10",
                         "resume_claimed_preupload_v11",
                         "resume_claimed_preupload_v12"}))
    if mode == "readback":
        if len(attempts) != 1:
            return _fail("plan8_final_v8_readback_attempt_ambiguous",
                         attempt_count=len(attempts))
        existing = attempts[0]
        if not existing.write_claimed:
            return _fail("plan8_final_v8_readback_attempt_not_found")
        return _readback_existing(db, plan, existing)
    is_preclaim_resume = mode == "resume_preclaim_v3"
    if is_preclaim_resume:
        if len(attempts) != 1:
            return _fail("plan8_final_v8_preclaim_attempt_ambiguous",
                         attempt_count=len(attempts))
        resume_ok, resume_detail = _validate_preclaim_resume_attempt(attempts[0])
        if not resume_ok:
            return _fail("plan8_final_v8_preclaim_attempt_mismatch",
                         attempt=resume_detail)
    elif attempts:
        if len(attempts) != 1:
            return _fail("plan8_final_v8_attempt_scope_ambiguous",
                         attempt_count=len(attempts))
        existing = attempts[0]
        if existing.state == "completed":
            manifest = (existing.result_summary or {}).get("manifest")
            if (not isinstance(manifest, dict)
                    or v6._hash(manifest) != existing.scope_sha256):
                return _fail("plan8_final_v8_attempt_scope_mismatch",
                             attempt_id=existing.id)
            return {"ok": True, "idempotent_replay": True,
                    "attempt_id": existing.id, "workflow_key": WORKFLOW_KEY,
                    "plan_id": PLAN_ID, "plan_status": plan.status,
                    "result": existing.result_summary or {},
                    "execution_boundary": _boundary(platform_write=False)}
        return _fail("plan8_final_v8_already_claimed_no_retry",
                     attempt_id=existing.id, attempt_state=existing.state,
                     platform_write_observed=existing.platform_write_observed)
    if plan.status != EXPECTED_STATUS:
        return _fail("plan8_final_v8_status_cas_mismatch",
                     actual_status=plan.status)
    prerequisite_ok, prerequisite = _validate_prerequisite(db)
    if not prerequisite_ok:
        return _fail("plan8_final_v8_prerequisite_attempt_mismatch",
                     attempt=prerequisite)
    policy_sha = str(campaign_policy_service.require_policy().get("_sha256") or "")
    if policy_sha != EXPECTED_POLICY_SHA256:
        return _fail("plan8_final_v8_policy_changed",
                     actual_policy_sha256=policy_sha)
    target_rows, scope_error = v7._target_rows(db, plan, identity, policy_sha)
    if scope_error:
        return _fail(**scope_error)
    discount_rows, discount_error = v7._discount_scope(db, plan)
    if discount_error:
        return _fail(**discount_error)
    manifest = _fixed_manifest(target_rows, discount_rows, policy_sha)
    inspect_scope_sha = v6._hash(manifest)
    db.commit()

    inspection = web_agent_service.recover_plan8_final_v8(
        db, payload={"phase": "inspect", "scope_sha256": inspect_scope_sha,
                     "manifest": manifest})
    if inspection.get("busy") or inspection.get("pre_write_busy"):
        return _fail("plan8_final_v8_pre_write_busy",
                     busy=inspection, write_claim_created=False)
    inspection_ok, inspection_detail = validate_inspection(
        inspection, manifest, inspect_scope_sha)
    if not inspection_ok:
        return _fail("plan8_final_v8_inspection_blocked",
                     inspection=inspection_detail,
                     need_scan=bool(inspection.get("need_scan")))
    if is_preclaim_resume and inspection_detail.get("v8_claim_absent") is not True:
        return _fail("plan8_final_v8_preclaim_resume_not_proven_safe",
                     inspection=inspection_detail,
                     write_claim_created=False)
    reservation_token = str(inspection["reservation_token"])
    manifest = v6.enrich_manifest_with_inspection(
        manifest, inspection_detail,
        inspect_scope_sha256=inspect_scope_sha)
    manifest_sha = v6._hash(manifest)

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None or plan.status != EXPECTED_STATUS:
        return _fail("plan8_final_v8_state_changed_after_reservation",
                     actual_status=getattr(plan, "status", None))
    post_identity_ok, post_identity = v6._identity_allowed(plan)
    post_policy_sha = str(
        campaign_policy_service.require_policy().get("_sha256") or "")
    post_rows, post_scope_error = v7._target_rows(
        db, plan, post_identity, post_policy_sha)
    post_discounts, post_discount_error = v7._discount_scope(db, plan)
    if (not post_identity_ok or post_policy_sha != policy_sha
            or post_scope_error or post_discount_error
            or v6._hash(_fixed_manifest(
                post_rows, post_discounts, post_policy_sha)) != inspect_scope_sha):
        return _fail("plan8_final_v8_erp_scope_changed_after_reservation",
                     identity=post_identity, policy_sha256=post_policy_sha,
                     signup_scope_error=post_scope_error,
                     discount_scope_error=post_discount_error)
    raced = _attempts(db)
    if is_preclaim_resume:
        if len(raced) != 1:
            return _fail("plan8_final_v8_preclaim_attempt_raced",
                         attempt_count=len(raced))
        attempt = db.execute(select(CampaignExecutionAttempt).where(
            CampaignExecutionAttempt.id == PRECLAIM_ATTEMPT_ID,
        ).with_for_update()).scalar_one_or_none()
        resume_ok, resume_detail = _validate_preclaim_resume_attempt(attempt)
        if not resume_ok:
            return _fail("plan8_final_v8_preclaim_attempt_changed",
                         attempt=resume_detail)
        prior = dict(attempt.result_summary or {})
        attempt.scope_sha256 = manifest_sha
        attempt.state = "write_claimed"
        attempt.write_claimed = True
        attempt.write_claimed_at = datetime.now(timezone.utc)
        attempt.platform_write_observed = None
        attempt.automatic_retry_allowed = False
        attempt.last_step = "platform_write_claim_preclaim_resume_v3"
        attempt.error_code = None
        attempt.web_agent_job_id = None
        attempt.result_summary = {
            "manifest": manifest,
            "inspection": inspection_detail,
            "preclaim_resume_source": {
                "attempt_id": PRECLAIM_ATTEMPT_ID,
                "scope_sha256": PRECLAIM_SCOPE_SHA256,
                "request_id": PRECLAIM_REQUEST_ID,
                "web_agent_job_id": PRECLAIM_WEB_AGENT_JOB_ID,
                "prior_last_step": PRECLAIM_LAST_STEP,
                "prior_error_code": PRECLAIM_ERROR_CODE,
                "prior_commit": prior.get("commit"),
            },
        }
    else:
        if raced:
            exact = _attempt_for_scope(db, manifest_sha)
            return _fail("plan8_final_v8_attempt_raced_no_write",
                         attempt_count=len(raced),
                         exact_scope_exists=exact is not None)
        attempt = CampaignExecutionAttempt(
            id=secrets.token_hex(12), plan_id=PLAN_ID,
            workflow_key=WORKFLOW_KEY,
            operation=OPERATION, scope_sha256=manifest_sha,
            state="write_claimed", write_claimed=True,
            write_claimed_at=datetime.now(timezone.utc),
            platform_write_observed=None, automatic_retry_allowed=False,
            request_id=f"plan8-final-v8-{secrets.token_hex(8)}",
            last_step="platform_write_claim",
            result_summary={"manifest": manifest,
                            "inspection": inspection_detail})
        db.add(attempt)
    plan.status = "resume_executing"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("plan8_final_v8_atomic_claim_conflict_no_write")
    return _commit_and_readback(
        db, plan=plan, attempt=attempt, manifest=manifest,
        manifest_sha=manifest_sha, inspect_scope_sha=inspect_scope_sha,
        reservation_token=reservation_token,
        inspection_detail=inspection_detail)

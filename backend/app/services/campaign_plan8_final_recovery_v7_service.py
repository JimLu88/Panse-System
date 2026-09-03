"""One-shot Plan 8 recovery using a new exact-window discount activity."""
from __future__ import annotations

from datetime import datetime, timezone
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_execution_service,
    campaign_plan8_final_recovery_v6_service as v6,
    campaign_policy_service,
    campaign_service,
    web_agent_service,
)


WORKFLOW_KEY = v6.WORKFLOW_KEY
PLAN_ID = v6.PLAN_ID
EXPECTED_STATUS = v6.EXPECTED_STATUS
RECOVERY_VERSION = 7
OPERATION = "plan8_final_recovery_v7"
EXECUTION_SOURCE = "campaign_super88_plan8_final_recovery_v7"
EXPECTED_POLICY_SHA256 = v6.EXPECTED_POLICY_SHA256
EXPECTED_TARGET_SCOPE_SHA256 = v6.EXPECTED_TARGET_SCOPE_SHA256
IDENTITY = v6.IDENTITY
DRAFT_RECORDS = v6.DRAFT_RECORDS
PROTECTED_RECORDS = v6.PROTECTED_RECORDS
TARGET_ITEM_IDS = v6.TARGET_ITEM_IDS
ADD_PAIRS = v6.ADD_PAIRS
EXPECTED_DISCOUNT_DEDUCTS = v6.EXPECTED_DISCOUNT_DEDUCTS
OLD_DISCOUNT_ACTIVITY_ID = v6.DISCOUNT_ACTIVITY_ID
EXPECTED_TARGET_CUSTOM_ROW_COUNT = v6.EXPECTED_TARGET_CUSTOM_ROW_COUNT
EXPECTED_COMMIT_CHECKPOINTS = v6.EXPECTED_COMMIT_CHECKPOINTS
READBACK_PLAN_STATUSES = v6.READBACK_PLAN_STATUSES
EXECUTE_CONFIRMATION = "EXECUTE_ONCE_PLAN8_V7_NEW_ACTIVITY_6_ITEMS_78_SKUS"
READBACK_CONFIRMATION = "READBACK_ONLY_PLAN8_V7_NO_PLATFORM_WRITE"
V6_ATTEMPT_ID = "1e764d94df9c82c2e974a3c4"
RECOVERY_EVIDENCE = {
    "v6_attempt_id": V6_ATTEMPT_ID,
    "v6_operation": "plan8_final_recovery_v6",
    "v6_state": "unknown_no_retry",
    "v6_last_checkpoint": "discount_terminal",
    "v6_import_ok": 0,
    "v6_import_failed": 8,
    "v6_error_artifact_sha256":
        "fd81df085b68ab9f76f52e52f48636452349f433bf804feaf1739256931c3cba",
    "fresh_product_export_sha256":
        "d7094b54bd3c984dbf5b319f565ab2c885f437b50729867474e3a7e1255cd5a9",
    "official_error_reason": "参数错误:skuId不是商品的有效sku",
}


def _boundary(*, platform_write: bool = False) -> dict:
    return {**v6._boundary(platform_write=platform_write),
            "activity_create": platform_write,
            "existing_activity_edit": False,
            "old_activity_id": OLD_DISCOUNT_ACTIVITY_ID}


def _fail(error: str, **detail) -> dict:
    return {"ok": False, "error": error, **detail,
            "execution_boundary": _boundary(platform_write=False)}


def _fixed_manifest(target_rows: list[dict], discount_rows: list[dict],
                    policy_sha: str) -> dict:
    manifest = v6._fixed_manifest(target_rows, discount_rows, policy_sha)
    manifest["recovery_version"] = RECOVERY_VERSION
    manifest["recovery_evidence"] = dict(RECOVERY_EVIDENCE)
    manifest["execution_order"] = [
        "create_new_8_sku_single_item_discount_activity",
        "add_4_plus_4_skus_to_two_bound_drafts",
        "publish_6_bound_drafts", "official_readback",
    ]
    return manifest


def _validate_prerequisites(db: Session) -> tuple[bool, list[dict]]:
    prior_ok, detail = v6._validate_prerequisites(db)
    row = db.get(CampaignExecutionAttempt, V6_ATTEMPT_ID)
    current = {
        "attempt_id": V6_ATTEMPT_ID,
        "operation": getattr(row, "operation", None),
        "state": getattr(row, "state", None),
        "write_claimed": getattr(row, "write_claimed", None),
    }
    detail.append(current)
    return bool(prior_ok and row is not None
                and current["operation"] == "plan8_final_recovery_v6"
                and current["state"] == "unknown_no_retry"
                and current["write_claimed"] is True), detail


def _new_activity_id(rows: list[dict]) -> str | None:
    ids = {str(row.get("activity_id") or "") for row in rows}
    if (len(rows) == 8 and len(ids) == 1
            and OLD_DISCOUNT_ACTIVITY_ID not in ids
            and next(iter(ids), "").isdigit()):
        return next(iter(ids))
    return None


def validate_inspection(result: dict, manifest: dict,
                        manifest_sha256: str) -> tuple[bool, dict]:
    ok, detail = v6.validate_inspection(result, manifest, manifest_sha256)
    evidence = result.get("recovery_evidence") or {}
    evidence_ok = bool(
        evidence.get("ok") is True
        and evidence.get("v6_attempt_id") == V6_ATTEMPT_ID
        and evidence.get("error_artifact_sha256")
        == RECOVERY_EVIDENCE["v6_error_artifact_sha256"]
        and evidence.get("fresh_product_export_sha256")
        == RECOVERY_EVIDENCE["fresh_product_export_sha256"])
    return bool(ok and evidence_ok), {**detail,
                                     "recovery_evidence": evidence}


def validate_commit(result: dict, manifest: dict,
                    manifest_sha256: str) -> tuple[bool, dict]:
    ok, detail = v6.validate_commit(result, manifest, manifest_sha256)
    activity_id = str(result.get("new_discount_activity_id") or "")
    activity_ok = bool(activity_id.isdigit()
                       and activity_id != OLD_DISCOUNT_ACTIVITY_ID)
    return bool(ok and activity_ok), {
        **detail, "new_discount_activity_id": activity_id,
        "new_discount_activity_ok": activity_ok,
    }


def validate_readback(result: dict, manifest: dict,
                      manifest_sha256: str) -> tuple[bool, dict]:
    original_rows = result.get("discount_rows") or []
    activity_id = _new_activity_id(original_rows)
    rewritten = {
        **result,
        "discount_rows": [
            {**row, "activity_id": OLD_DISCOUNT_ACTIVITY_ID}
            for row in original_rows
        ],
    }
    base_ok, detail = v6.validate_readback(
        rewritten, manifest, manifest_sha256)
    return bool(base_ok and activity_id is not None), {
        **detail,
        "discount_rows": original_rows,
        "new_discount_activity_id": activity_id,
        "old_discount_activity_id": OLD_DISCOUNT_ACTIVITY_ID,
    }


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


def verify_plan8_final_v7_claim(
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
        and manifest.get("recovery_evidence") == RECOVERY_EVIDENCE
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


def _readback_existing(db: Session, plan: CampaignPlan,
                       attempt: CampaignExecutionAttempt) -> dict:
    if str(plan.status or "") not in READBACK_PLAN_STATUSES:
        return _fail("plan8_final_v7_readback_plan_status_not_allowed",
                     actual_status=plan.status, attempt_id=attempt.id)
    manifest = (attempt.result_summary or {}).get("manifest")
    if not isinstance(manifest, dict):
        return _fail("plan8_final_v7_attempt_manifest_missing",
                     attempt_id=attempt.id)
    if v6._hash(manifest) != attempt.scope_sha256:
        return _fail("plan8_final_v7_attempt_scope_mismatch",
                     attempt_id=attempt.id)
    try:
        result = web_agent_service.recover_plan8_final_v7(
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
        return _fail("plan8_final_v7_readback_not_complete",
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


def recover_plan8_final_v7(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, recovery_version: int,
        mode: str = "execute", confirmation: str = "",
        target_scope_sha256: str = "") -> dict:
    expected_confirmation = (
        EXECUTE_CONFIRMATION if mode == "execute" else READBACK_CONFIRMATION)
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_status != EXPECTED_STATUS
            or recovery_version != RECOVERY_VERSION
            or mode not in {"execute", "readback"}
            or confirmation != expected_confirmation
            or target_scope_sha256 != EXPECTED_TARGET_SCOPE_SHA256):
        return _fail("plan8_final_v7_request_not_allowed")
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    identity_ok, identity = v6._identity_allowed(plan)
    if not identity_ok:
        return _fail("plan8_final_v7_identity_not_allowed", identity=identity)
    attempts = _attempts(db)
    if mode == "readback":
        if len(attempts) != 1:
            return _fail("plan8_final_v7_readback_attempt_ambiguous",
                         attempt_count=len(attempts))
        existing = attempts[0]
        if not existing.write_claimed:
            return _fail("plan8_final_v7_readback_attempt_not_found")
        return _readback_existing(db, plan, existing)
    if attempts:
        if len(attempts) != 1:
            return _fail("plan8_final_v7_attempt_scope_ambiguous",
                         attempt_count=len(attempts))
        existing = attempts[0]
        if existing.state == "completed":
            manifest = (existing.result_summary or {}).get("manifest")
            if not isinstance(manifest, dict) or v6._hash(manifest) != existing.scope_sha256:
                return _fail("plan8_final_v7_attempt_scope_mismatch",
                             attempt_id=existing.id)
            return {"ok": True, "idempotent_replay": True,
                    "attempt_id": existing.id, "workflow_key": WORKFLOW_KEY,
                    "plan_id": PLAN_ID, "plan_status": plan.status,
                    "result": existing.result_summary or {},
                    "execution_boundary": _boundary(platform_write=False)}
        return _fail("plan8_final_v7_already_claimed_no_retry",
                     attempt_id=existing.id, attempt_state=existing.state,
                     platform_write_observed=existing.platform_write_observed)
    if plan.status != EXPECTED_STATUS:
        return _fail("plan8_final_v7_status_cas_mismatch",
                     actual_status=plan.status)
    prerequisites_ok, prerequisite_detail = _validate_prerequisites(db)
    if not prerequisites_ok:
        return _fail("plan8_final_v7_prerequisite_attempt_mismatch",
                     attempts=prerequisite_detail)
    policy_sha = str(campaign_policy_service.require_policy().get("_sha256") or "")
    if policy_sha != EXPECTED_POLICY_SHA256:
        return _fail("plan8_final_v7_policy_changed",
                     actual_policy_sha256=policy_sha)
    target_rows, scope_error = v6._target_rows(db, plan, identity, policy_sha)
    if scope_error:
        return _fail(**scope_error)
    discount_rows, discount_error = v6._discount_scope(db, plan)
    if discount_error:
        return _fail(**discount_error)
    manifest = _fixed_manifest(target_rows, discount_rows, policy_sha)
    inspect_scope_sha = v6._hash(manifest)
    db.commit()

    inspection = web_agent_service.recover_plan8_final_v7(
        db, payload={"phase": "inspect", "scope_sha256": inspect_scope_sha,
                     "manifest": manifest})
    if inspection.get("busy") or inspection.get("pre_write_busy"):
        return _fail("plan8_final_v7_pre_write_busy",
                     busy=inspection, write_claim_created=False)
    inspection_ok, inspection_detail = validate_inspection(
        inspection, manifest, inspect_scope_sha)
    if not inspection_ok:
        return _fail("plan8_final_v7_inspection_blocked",
                     inspection=inspection_detail,
                     need_scan=bool(inspection.get("need_scan")))
    reservation_token = str(inspection["reservation_token"])
    manifest = v6.enrich_manifest_with_inspection(
        manifest, inspection_detail, inspect_scope_sha256=inspect_scope_sha)
    manifest_sha = v6._hash(manifest)
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None or plan.status != EXPECTED_STATUS:
        return _fail("plan8_final_v7_state_changed_after_reservation",
                     actual_status=getattr(plan, "status", None))
    post_identity_ok, post_identity = v6._identity_allowed(plan)
    post_policy_sha = str(
        campaign_policy_service.require_policy().get("_sha256") or "")
    post_rows, post_scope_error = v6._target_rows(
        db, plan, post_identity, post_policy_sha)
    post_discounts, post_discount_error = v6._discount_scope(db, plan)
    if (not post_identity_ok or post_policy_sha != policy_sha
            or post_scope_error or post_discount_error
            or v6._hash(_fixed_manifest(
                post_rows, post_discounts, post_policy_sha)) != inspect_scope_sha):
        return _fail("plan8_final_v7_erp_scope_changed_after_reservation",
                     identity=post_identity, policy_sha256=post_policy_sha,
                     signup_scope_error=post_scope_error,
                     discount_scope_error=post_discount_error)
    raced = _attempts(db)
    if raced:
        exact = _attempt_for_scope(db, manifest_sha)
        return _fail("plan8_final_v7_attempt_raced_no_write",
                     attempt_count=len(raced), exact_scope_exists=exact is not None)
    attempt = CampaignExecutionAttempt(
        id=secrets.token_hex(12), plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=OPERATION, scope_sha256=manifest_sha,
        state="write_claimed", write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc),
        platform_write_observed=None, automatic_retry_allowed=False,
        request_id=f"plan8-final-v7-{secrets.token_hex(8)}",
        last_step="platform_write_claim",
        result_summary={"manifest": manifest, "inspection": inspection_detail})
    db.add(attempt)
    plan.status = "resume_executing"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("plan8_final_v7_atomic_claim_conflict_no_write")
    claim_verification = {
        "attempt_id": attempt.id, "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID, "operation": OPERATION,
        "scope_sha256": manifest_sha,
        "inspect_scope_sha256": inspect_scope_sha,
        "reservation_token_sha256": inspection_detail[
            "reservation_token_sha256"],
    }
    try:
        committed = web_agent_service.recover_plan8_final_v7(
            db, payload={"phase": "commit", "scope_sha256": manifest_sha,
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
            step=str(committed.get("step") or "plan8_final_v7_commit"),
            error_code=str(committed.get("error") or "commit_failed"),
            job_id=str(committed.get("web_agent_job_id") or "") or None,
            result_summary={"manifest": manifest,
                            "inspection": inspection_detail,
                            "commit": commit_detail})
        return _fail("plan8_final_v7_commit_failed_no_retry",
                     attempt_id=attempt.id, commit=commit_detail)
    try:
        readback = web_agent_service.recover_plan8_final_v7(
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
            step="plan8_final_v7_readback",
            error_code="post_submit_readback_not_complete",
            job_id=str(readback.get("web_agent_job_id") or "") or None,
            result_summary={"manifest": manifest,
                            "inspection": inspection_detail,
                            "commit": committed, "readback": readback_detail})
        return _fail("plan8_final_v7_readback_not_complete",
                     attempt_id=attempt.id, readback=readback_detail)
    campaign_execution_service.record_platform_terminal(
        db, attempt, state="completed", platform_write_observed=True,
        step="readback_verified",
        job_id=str(readback.get("web_agent_job_id") or "") or None,
        result_summary={"manifest": manifest,
                        "inspection": inspection_detail,
                        "commit": commit_detail, "readback": readback_detail,
                        "finished_at": datetime.now(timezone.utc).isoformat()})
    plan.status = "reconciled"
    db.commit()
    return {"ok": True, "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
            "plan_status": plan.status, "attempt_id": attempt.id,
            "scope_sha256": manifest_sha, "verification": readback_detail,
            "execution_boundary": _boundary(platform_write=True)}

"""One-shot time-only update for the three approved plan-7 discount activities."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import (
    CampaignEvidenceSnapshot,
    CampaignExecutionAttempt,
    CampaignPlan,
)
from app.services import (
    campaign_discount_audit_service,
    campaign_service,
    web_agent_service,
)


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
ACTIVITY_IDS = (
    "143780562424",
    "143936811502",
    "143939511827",
)
EXPECTED_START_AT = "2026-09-01 00:00:00"
EXPECTED_END_AT = "2026-09-01 23:59:59"
TARGET_START_AT = "2026-09-01 00:00:00"
TARGET_END_AT = "2026-09-05 23:59:59"
EXPECTED_SCOPE_SHA256 = (
    "38c967e5a08acd378ff6c4778494f450613926a9cf32e7ee51b51a1d81b75d8f"
)
EXPECTED_SCOPE_ROWS = 392
EXPECTED_SCOPE_ITEMS = 55
OPERATION = "discount_time_update"
RECOVERY_OPERATION = "discount_time_update_recovery"
RECOVERY_FAILED_ATTEMPT_ID = "9cf79b441a5fdbd56de061a7"
RECOVERY_FAILED_WEB_AGENT_JOB_ID = "job2"
RECOVERY_PREWRITE_RECEIPTS = (
    {
        "request_id": "feb0a38dc0ec",
        "web_agent_job_id": "job1",
        "attempt_id": None,
        "platform_write": False,
        "submitted": False,
        "confirmed_activity_ids": (),
    },
    {
        "request_id": "32110b92632a",
        "web_agent_job_id": RECOVERY_FAILED_WEB_AGENT_JOB_ID,
        "attempt_id": RECOVERY_FAILED_ATTEMPT_ID,
        "platform_write": False,
        "submitted": False,
        "confirmed_activity_ids": (),
    },
)


def _fmt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _boundary(*, platform_read: bool = False,
              platform_write: bool = False) -> dict:
    return {
        "plan7_only": True,
        "allowed_activity_ids": list(ACTIVITY_IDS),
        "platform_read": bool(platform_read),
        "platform_write": bool(platform_write),
        "account_action": bool(platform_write),
        "time_change_only": True,
        "price_change": False,
        "sku_change": False,
        "product_change": False,
        "scope_change": False,
        "discount_change": False,
        "withdraw_pause_remove": False,
        "create_activity": False,
        "touches_plan8": False,
        "notification": False,
        "automatic_retry": False,
    }


def normalize_request(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("plan7_discount_time_update_request_required")
    allowed = {
        "workflow_key", "plan_id", "activity_ids", "expected_start_at",
        "expected_end_at", "target_start_at", "target_end_at",
    }
    if set(value) != allowed:
        raise ValueError("plan7_discount_time_update_request_fields_invalid")
    ids = value.get("activity_ids")
    if not isinstance(ids, list):
        raise ValueError("plan7_discount_time_update_activity_ids_invalid")
    normalized = {
        "workflow_key": str(value.get("workflow_key") or "").strip(),
        "plan_id": value.get("plan_id"),
        "activity_ids": tuple(str(item or "").strip() for item in ids),
        "expected_start_at": str(value.get("expected_start_at") or "").strip(),
        "expected_end_at": str(value.get("expected_end_at") or "").strip(),
        "target_start_at": str(value.get("target_start_at") or "").strip(),
        "target_end_at": str(value.get("target_end_at") or "").strip(),
    }
    expected = {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "activity_ids": ACTIVITY_IDS,
        "expected_start_at": EXPECTED_START_AT,
        "expected_end_at": EXPECTED_END_AT,
        "target_start_at": TARGET_START_AT,
        "target_end_at": TARGET_END_AT,
    }
    if normalized != expected:
        raise ValueError("plan7_discount_time_update_request_not_allowed")
    return {**normalized, "activity_ids": list(ACTIVITY_IDS)}


def normalize_recovery_request(value: dict) -> dict:
    """Accept only the two known write-free receipts and failed attempt."""
    if not isinstance(value, dict):
        raise ValueError("plan7_discount_time_recovery_request_required")
    base_keys = {
        "workflow_key", "plan_id", "activity_ids", "expected_start_at",
        "expected_end_at", "target_start_at", "target_end_at",
    }
    if set(value) != base_keys | {"failed_attempt_id", "prewrite_receipts"}:
        raise ValueError("plan7_discount_time_recovery_fields_invalid")
    base = normalize_request({key: value.get(key) for key in base_keys})
    if str(value.get("failed_attempt_id") or "").strip() != RECOVERY_FAILED_ATTEMPT_ID:
        raise ValueError("plan7_discount_time_recovery_attempt_mismatch")
    receipts = value.get("prewrite_receipts")
    if not isinstance(receipts, list) or len(receipts) != 2:
        raise ValueError("plan7_discount_time_recovery_receipts_invalid")
    normalized_receipts = []
    allowed_receipt_keys = {
        "request_id", "web_agent_job_id", "attempt_id", "platform_write",
        "submitted", "confirmed_activity_ids",
    }
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != allowed_receipt_keys:
            raise ValueError("plan7_discount_time_recovery_receipts_invalid")
        confirmed = receipt.get("confirmed_activity_ids")
        if not isinstance(confirmed, list):
            raise ValueError("plan7_discount_time_recovery_receipts_invalid")
        normalized_receipts.append({
            "request_id": str(receipt.get("request_id") or "").strip(),
            "web_agent_job_id": str(
                receipt.get("web_agent_job_id") or "").strip(),
            "attempt_id": (
                str(receipt.get("attempt_id") or "").strip() or None),
            "platform_write": receipt.get("platform_write"),
            "submitted": receipt.get("submitted"),
            "confirmed_activity_ids": tuple(
                str(item or "").strip() for item in confirmed),
        })
    if tuple(normalized_receipts) != RECOVERY_PREWRITE_RECEIPTS:
        raise ValueError("plan7_discount_time_recovery_receipts_mismatch")
    return {
        **base,
        "failed_attempt_id": RECOVERY_FAILED_ATTEMPT_ID,
        "prewrite_receipts": [
            {**receipt, "confirmed_activity_ids": list(
                receipt["confirmed_activity_ids"])}
            for receipt in normalized_receipts
        ],
    }


def manifest_sha256(payload: dict) -> str:
    canonical = normalize_request(payload)
    raw = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _web_payload(payload: dict, phase: str) -> dict:
    return {**normalize_request(payload), "phase": phase}


def _validate_plan_and_scope(db: Session) -> tuple[CampaignPlan | None, dict | None]:
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    )).scalar_one_or_none()
    if plan is None:
        return None, {"error": "workflow_not_found"}
    if (plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or _fmt(plan.start_at) != EXPECTED_START_AT
            or _fmt(plan.end_at) != EXPECTED_END_AT):
        return plan, {"error": "plan7_discount_time_update_plan_identity_drift"}
    scope = campaign_discount_audit_service._scope_rows(db, plan)
    digest = campaign_discount_audit_service.scope_sha256(scope)
    item_count = len({row["item_id"] for row in scope})
    if (digest != EXPECTED_SCOPE_SHA256
            or len(scope) != EXPECTED_SCOPE_ROWS
            or item_count != EXPECTED_SCOPE_ITEMS):
        return plan, {
            "error": "plan7_discount_time_update_scope_drift",
            "actual_scope_sha256": digest,
            "actual_rows": len(scope),
            "actual_items": item_count,
        }
    return plan, None


def _existing_attempt(
        db: Session, scope_sha256: str, *, operation: str = OPERATION):
    return db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == operation,
        CampaignExecutionAttempt.scope_sha256 == scope_sha256,
    )).scalar_one_or_none()


def _attempt_replay(attempt: CampaignExecutionAttempt) -> dict:
    summary = dict(attempt.result_summary or {})
    completed = attempt.state == "completed"
    return {
        "ok": completed,
        "error": None if completed else "plan7_discount_time_update_already_claimed",
        "idempotent_replay": True,
        "attempt_id": attempt.id,
        "request_id": attempt.request_id,
        "attempt_state": attempt.state,
        "platform_write_observed": attempt.platform_write_observed,
        "automatic_retry": False,
        "activities": summary.get("activities") or [],
        "execution_boundary": summary.get("execution_boundary") or _boundary(
            platform_read=True,
            platform_write=bool(attempt.platform_write_observed)),
    }


def _validate_preflight(result: dict) -> bool:
    activities = result.get("activities") or []
    return bool(
        result.get("ok")
        and [row.get("activity_id") for row in activities] == list(ACTIVITY_IDS)
        and all(
            row.get("start_at") == EXPECTED_START_AT
            and row.get("end_at") == EXPECTED_END_AT
            and row.get("sku_level") is True
            and row.get("discount_mode") == "减钱"
            and row.get("editable") is True
            for row in activities
        )
        and not (result.get("execution_boundary") or {}).get("platform_write")
    )


def _safe_result_summary(result: dict) -> dict:
    boundary = dict(result.get("execution_boundary") or {})
    return {
        "ok": bool(result.get("ok")),
        "phase": result.get("phase"),
        "error": result.get("error"),
        "step": result.get("step"),
        "submitted": bool(result.get("submitted")),
        "web_agent_job_id": result.get("web_agent_job_id"),
        "confirmed_activity_ids": result.get("confirmed_activity_ids") or [],
        "activities": result.get("activities") or [],
        "execution_boundary": boundary,
    }


def update_plan7_single_discount_times(db: Session, *, request_payload: dict) -> dict:
    """Pre-read, durably claim, update once, then require exact readback."""
    try:
        payload = normalize_request(request_payload)
    except ValueError as exc:
        return {
            "ok": False, "error": str(exc),
            "execution_boundary": _boundary(),
        }
    plan, problem = _validate_plan_and_scope(db)
    if problem:
        return {
            "ok": False, **problem,
            "execution_boundary": _boundary(),
        }
    digest = manifest_sha256(payload)
    existing = _existing_attempt(db, digest)
    if existing is not None:
        return _attempt_replay(existing)

    # No mutation claim exists yet.  This first job is strictly read-only and
    # may be safely invoked again if connectivity or login fails.
    preflight = web_agent_service.update_plan7_single_discount_times(
        db, payload=_web_payload(payload, "preflight"))
    if not _validate_preflight(preflight):
        return {
            "ok": False,
            "error": preflight.get("error") or "plan7_discount_time_update_preflight_failed",
            "step": preflight.get("step") or "preflight",
            "safe_retry_before_write": True,
            "web_agent_job_id": preflight.get("web_agent_job_id"),
            "activities": preflight.get("activities") or [],
            "execution_boundary": _boundary(platform_read=True),
        }

    attempt = CampaignExecutionAttempt(
        id=secrets.token_hex(12),
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        operation=OPERATION,
        scope_sha256=digest,
        state="claimed",
        write_claimed=True,
        write_claimed_at=datetime.now().astimezone(),
        platform_write_observed=None,
        automatic_retry_allowed=False,
        request_id=f"plan7-time-update-{secrets.token_hex(8)}",
        last_step="preflight_complete_write_claimed",
        result_summary={
            "preflight_activities": preflight.get("activities") or [],
            "execution_boundary": _boundary(platform_read=True),
        },
    )
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = _existing_attempt(db, digest)
        if raced is not None:
            return _attempt_replay(raced)
        raise

    result = web_agent_service.update_plan7_single_discount_times(
        db, payload=_web_payload(payload, "commit"))
    boundary = dict(result.get("execution_boundary") or {})
    write_observed = bool(
        boundary.get("platform_write")
        or result.get("submitted")
        or result.get("confirmed_activity_ids")
    )
    activities = result.get("activities") or []
    exact_terminal = bool(
        result.get("ok")
        and [row.get("activity_id") for row in activities] == list(ACTIVITY_IDS)
        and all(
            row.get("after_start_at") == TARGET_START_AT
            and row.get("after_end_at") == TARGET_END_AT
            and row.get("platform_terminal") == "updated_and_readback_exact"
            for row in activities
        )
    )
    attempt.state = (
        "completed" if exact_terminal
        else "unknown" if write_observed
        else "failed"
    )
    attempt.platform_write_observed = write_observed
    attempt.web_agent_job_id = result.get("web_agent_job_id")
    attempt.last_step = (
        "post_readback_complete" if exact_terminal
        else result.get("step") or "commit_failed")
    attempt.error_code = None if exact_terminal else str(
        result.get("error") or "plan7_discount_time_update_terminal_not_exact")[:128]
    summary = _safe_result_summary(result)
    attempt.result_summary = summary
    snapshot = CampaignEvidenceSnapshot(
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        evidence_type="plan7_discount_time_update_terminal",
        request_id=attempt.request_id,
        web_agent_job_id=attempt.web_agent_job_id,
        scope_sha256=digest,
        result_status=attempt.state,
        platform_summary={
            "expected_start_at": EXPECTED_START_AT,
            "expected_end_at": EXPECTED_END_AT,
            "target_start_at": TARGET_START_AT,
            "target_end_at": TARGET_END_AT,
            "platform_write_observed": write_observed,
        },
        rows=activities,
        failure_rows=[],
        execution_boundary=boundary or _boundary(
            platform_read=True, platform_write=write_observed),
    )
    db.add(snapshot)
    db.commit()
    return {
        **result,
        "ok": exact_terminal,
        "error": None if exact_terminal else (
            result.get("error") or "plan7_discount_time_update_terminal_not_exact"),
        "attempt_id": attempt.id,
        "request_id": attempt.request_id,
        "attempt_state": attempt.state,
        "manifest_sha256": digest,
        "plan7_discount_scope_sha256": EXPECTED_SCOPE_SHA256,
        "automatic_retry": False,
        "execution_boundary": snapshot.execution_boundary,
    }


def _validate_recovery_prewrite_evidence(
        db: Session, *, scope_sha256: str) -> tuple[
            CampaignExecutionAttempt | None, dict | None]:
    """Prove the claimed original attempt reached no platform-write boundary."""
    attempt = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == RECOVERY_FAILED_ATTEMPT_ID,
    )).scalar_one_or_none()
    if attempt is None:
        return None, {"error": "plan7_discount_time_recovery_attempt_not_found"}
    original = _existing_attempt(db, scope_sha256, operation=OPERATION)
    if original is None or original.id != attempt.id:
        return attempt, {"error": "plan7_discount_time_recovery_attempt_identity_drift"}
    summary = dict(attempt.result_summary or {})
    boundary = dict(summary.get("execution_boundary") or {})
    expected_error_marker = (
        "活动 143780562424 在活动列表中不是唯一记录（0条）"
    )
    if not (
        attempt.plan_id == PLAN_ID
        and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION
        and attempt.scope_sha256 == scope_sha256
        and attempt.state == "failed"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.automatic_retry_allowed is False
        and attempt.web_agent_job_id == RECOVERY_FAILED_WEB_AGENT_JOB_ID
        and attempt.last_step == "cas_pre_read"
        and summary.get("step") == "cas_pre_read"
        and summary.get("submitted") in (None, False)
        and not (summary.get("confirmed_activity_ids") or [])
        and not (summary.get("activities") or [])
        and boundary.get("platform_write") is False
        and not bool(boundary.get("account_action"))
        and expected_error_marker in str(summary.get("error") or "")
    ):
        return attempt, {"error": "plan7_discount_time_recovery_write_free_proof_failed"}
    snapshots = db.execute(select(CampaignEvidenceSnapshot).where(
        CampaignEvidenceSnapshot.plan_id == PLAN_ID,
        CampaignEvidenceSnapshot.workflow_key == WORKFLOW_KEY,
        CampaignEvidenceSnapshot.evidence_type
        == "plan7_discount_time_update_terminal",
        CampaignEvidenceSnapshot.request_id == attempt.request_id,
    )).scalars().all()
    if len(snapshots) != 1:
        return attempt, {"error": "plan7_discount_time_recovery_snapshot_not_unique"}
    snapshot = snapshots[0]
    snapshot_boundary = dict(snapshot.execution_boundary or {})
    platform_summary = dict(snapshot.platform_summary or {})
    if not (
        snapshot.result_status == "failed"
        and snapshot.web_agent_job_id == RECOVERY_FAILED_WEB_AGENT_JOB_ID
        and not (snapshot.rows or [])
        and snapshot_boundary.get("platform_write") is False
        and not bool(snapshot_boundary.get("account_action"))
        and platform_summary.get("platform_write_observed") is False
    ):
        return attempt, {"error": "plan7_discount_time_recovery_snapshot_not_write_free"}
    return attempt, None


def _recovery_attempt_replay(attempt: CampaignExecutionAttempt) -> dict:
    summary = dict(attempt.result_summary or {})
    completed = attempt.state == "completed"
    return {
        "ok": completed,
        "error": None if completed else (
            "plan7_discount_time_recovery_already_claimed"),
        "idempotent_replay": True,
        "attempt_id": attempt.id,
        "request_id": attempt.request_id,
        "attempt_state": attempt.state,
        "platform_write_observed": attempt.platform_write_observed,
        "automatic_retry": False,
        "activities": summary.get("activities") or [],
        "execution_boundary": summary.get("execution_boundary") or _boundary(
            platform_read=True,
            platform_write=bool(attempt.platform_write_observed)),
    }


def recover_plan7_single_discount_times(
        db: Session, *, request_payload: dict) -> dict:
    """One recovery only after proving both known calls wrote nothing."""
    try:
        recovery = normalize_recovery_request(request_payload)
    except ValueError as exc:
        return {
            "ok": False, "error": str(exc),
            "execution_boundary": _boundary(),
        }
    base_payload = {key: recovery[key] for key in (
        "workflow_key", "plan_id", "activity_ids", "expected_start_at",
        "expected_end_at", "target_start_at", "target_end_at",
    )}
    plan, problem = _validate_plan_and_scope(db)
    if problem:
        return {"ok": False, **problem, "execution_boundary": _boundary()}
    digest = manifest_sha256(base_payload)
    existing = _existing_attempt(
        db, digest, operation=RECOVERY_OPERATION)
    if existing is not None:
        return _recovery_attempt_replay(existing)
    old_attempt, problem = _validate_recovery_prewrite_evidence(
        db, scope_sha256=digest)
    if problem:
        return {"ok": False, **problem, "execution_boundary": _boundary()}

    # Current old times are re-read through exact per-ID searches before a new
    # durable write claim exists. A zero result or any drift leaves no claim.
    preflight = web_agent_service.update_plan7_single_discount_times(
        db, payload=_web_payload(base_payload, "preflight"))
    if not _validate_preflight(preflight):
        return {
            "ok": False,
            "error": preflight.get("error") or (
                "plan7_discount_time_recovery_preflight_failed"),
            "step": preflight.get("step") or "recovery_preflight",
            "safe_retry_before_write": False,
            "recovery_not_claimed": True,
            "web_agent_job_id": preflight.get("web_agent_job_id"),
            "activities": preflight.get("activities") or [],
            "execution_boundary": _boundary(platform_read=True),
        }

    attempt = CampaignExecutionAttempt(
        id=secrets.token_hex(12),
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        operation=RECOVERY_OPERATION,
        scope_sha256=digest,
        state="claimed",
        write_claimed=True,
        write_claimed_at=datetime.now().astimezone(),
        platform_write_observed=None,
        automatic_retry_allowed=False,
        request_id=f"plan7-time-recovery-{secrets.token_hex(8)}",
        last_step="recovery_preflight_complete_write_claimed",
        result_summary={
            "recovered_from_attempt_id": old_attempt.id,
            "prewrite_receipts": recovery["prewrite_receipts"],
            "preflight_activities": preflight.get("activities") or [],
            "execution_boundary": _boundary(platform_read=True),
        },
    )
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = _existing_attempt(
            db, digest, operation=RECOVERY_OPERATION)
        if raced is not None:
            return _recovery_attempt_replay(raced)
        raise

    result = web_agent_service.update_plan7_single_discount_times(
        db, payload=_web_payload(base_payload, "commit"))
    boundary = dict(result.get("execution_boundary") or {})
    write_observed = bool(
        boundary.get("platform_write")
        or result.get("submitted")
        or result.get("confirmed_activity_ids")
    )
    activities = result.get("activities") or []
    exact_terminal = bool(
        result.get("ok")
        and [row.get("activity_id") for row in activities] == list(ACTIVITY_IDS)
        and all(
            row.get("after_start_at") == TARGET_START_AT
            and row.get("after_end_at") == TARGET_END_AT
            and row.get("platform_terminal") == "updated_and_readback_exact"
            for row in activities
        )
    )
    attempt.state = (
        "completed" if exact_terminal
        else "unknown" if write_observed
        else "failed"
    )
    attempt.platform_write_observed = write_observed
    attempt.web_agent_job_id = result.get("web_agent_job_id")
    attempt.last_step = (
        "post_readback_complete" if exact_terminal
        else result.get("step") or "recovery_commit_failed")
    attempt.error_code = None if exact_terminal else str(
        result.get("error")
        or "plan7_discount_time_recovery_terminal_not_exact")[:128]
    summary = _safe_result_summary(result)
    summary["recovered_from_attempt_id"] = old_attempt.id
    summary["prewrite_receipts"] = recovery["prewrite_receipts"]
    attempt.result_summary = summary
    snapshot = CampaignEvidenceSnapshot(
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        evidence_type="plan7_discount_time_update_recovery_terminal",
        request_id=attempt.request_id,
        web_agent_job_id=attempt.web_agent_job_id,
        scope_sha256=digest,
        result_status=attempt.state,
        platform_summary={
            "expected_start_at": EXPECTED_START_AT,
            "expected_end_at": EXPECTED_END_AT,
            "target_start_at": TARGET_START_AT,
            "target_end_at": TARGET_END_AT,
            "platform_write_observed": write_observed,
            "recovered_from_attempt_id": old_attempt.id,
        },
        rows=activities,
        failure_rows=[],
        execution_boundary=boundary or _boundary(
            platform_read=True, platform_write=write_observed),
    )
    db.add(snapshot)
    db.commit()
    return {
        **result,
        "ok": exact_terminal,
        "error": None if exact_terminal else (
            result.get("error")
            or "plan7_discount_time_recovery_terminal_not_exact"),
        "attempt_id": attempt.id,
        "request_id": attempt.request_id,
        "attempt_state": attempt.state,
        "recovered_from_attempt_id": old_attempt.id,
        "manifest_sha256": digest,
        "plan7_discount_scope_sha256": EXPECTED_SCOPE_SHA256,
        "automatic_retry": False,
        "execution_boundary": snapshot.execution_boundary,
    }

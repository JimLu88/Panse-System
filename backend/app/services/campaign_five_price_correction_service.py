"""Durable one-shot claims for the reviewed five-item price correction."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import web_agent_service


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
MANIFEST_SHA256 = (
    "76be75d3ee7fbfcdb45d045a69fa2e0211c9757e4065a361407d22c57b2eda4c")
SOURCE_EXPORT_SHA256 = (
    "545a26af5dee4bf1fc0a016c207dea341b557e9e3e263c8e73b3f9c0c3d35366")
PHASES = {"single_discount", "super_reduce"}
OPERATION_BY_PHASE = {
    "single_discount": "five_price_single_discount",
    "super_reduce": "five_price_super_reduce",
}
RECOVERY_FAILED_ATTEMPT_ID = "b8b0ddcb5633cbe6a1b69681"
RECOVERY_OPERATION = "five_price_recover_single"
ZERO_SALES_EXCLUDED_ITEM_ID = "793202812082"


def request_payload(phase: str) -> dict:
    if phase not in PHASES:
        raise ValueError("five_price_phase_invalid")
    return {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "phase": phase,
        "manifest_sha256": MANIFEST_SHA256,
        "source_export_sha256": SOURCE_EXPORT_SHA256,
    }


def validate_request(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        return payload == request_payload(str(payload.get("phase") or ""))
    except ValueError:
        return False


def recovery_request_payload() -> dict:
    return {
        **request_payload("single_discount"),
        "expected_failed_attempt_id": RECOVERY_FAILED_ATTEMPT_ID,
        "confirmed_no_platform_write": True,
    }


def validate_recovery_request(payload: dict) -> bool:
    return isinstance(payload, dict) and payload == recovery_request_payload()


def _boundary(*, platform_write=False, phase=None) -> dict:
    return {
        "platform_read": True,
        "platform_write": platform_write,
        "account_action": platform_write,
        "phase": phase,
        "daily_product_price_change": False,
        "warehouse_price_change": False,
        "signup": False,
        "withdraw_pause_remove": False,
        "automatic_retry": False,
        "zero_sales_item_touched": False,
    }


def _fail(error: str, **extra) -> dict:
    phase = extra.pop("phase", None)
    observed = extra.pop("platform_write", False)
    return {"ok": False, "error": error,
            "execution_boundary": _boundary(
                platform_write=observed, phase=phase), **extra}


def _scope_sha(phase: str) -> str:
    raw = json.dumps(request_payload(phase), sort_keys=True,
                     separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _validate_plan(db: Session) -> dict | None:
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY)).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    if (plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or str(plan.qn_campaign_title or "").strip() != "超级立减"):
        return _fail("five_price_plan_identity_drift")
    return None


def _single_discount_completed(db: Session) -> bool:
    return db.execute(select(CampaignExecutionAttempt.id).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation.in_((
            OPERATION_BY_PHASE["single_discount"], RECOVERY_OPERATION)),
        CampaignExecutionAttempt.state == "completed",
        CampaignExecutionAttempt.platform_write_observed.in_((True, False)),
    )).first() is not None


def _write_free_timeout_attempt_exact(attempt: CampaignExecutionAttempt) -> bool:
    summary = attempt.result_summary if isinstance(
        attempt.result_summary, dict) else {}
    terminal_error = str(summary.get("terminal_error") or "")
    return bool(
        attempt.id == RECOVERY_FAILED_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID
        and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION_BY_PHASE["single_discount"]
        and attempt.scope_sha256 == _scope_sha("single_discount")
        and attempt.state == "unknown"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is None
        and attempt.web_agent_job_id is None
        and attempt.automatic_retry_allowed is False
        and attempt.last_step == "terminal_not_exact"
        and str(attempt.error_code or "").startswith("ConnectTimeout:")
        and summary.get("request") == request_payload("single_discount")
        and summary.get("terminal_ok") is False
        and summary.get("submitted") is None
        and "/api/campaign/five-price-correction" in terminal_error
        and "Connection to 192.168.31.91 timed out" in terminal_error
    )


def _terminal_exact(result: dict, phase: str) -> bool:
    boundary = result.get("execution_boundary") or {}
    common = (
        result.get("ok") is True
        and boundary.get("automatic_retry") is False
        and boundary.get("withdraw_pause_remove") is False
        and boundary.get("daily_product_price_change") is False
        and boundary.get("warehouse_price_change") is False
        and boundary.get("zero_sales_item_touched") is False
    )
    if phase == "single_discount":
        return common and (
            result.get("already_exact_no_write") is True
            or (result.get("submitted") is True
                and result.get("item_id") == "717418169535"
                and result.get("row_count") == 17
                and boundary.get("platform_write") is True))
    return common and result.get("item_count") == 4 \
        and result.get("target_sku_count") == 8 \
        and result.get("zero_sales_excluded_item_id") == ZERO_SALES_EXCLUDED_ITEM_ID


def execute(db: Session, *, payload: dict) -> dict:
    if not validate_request(payload):
        return _fail("five_price_request_not_allowed")
    phase = payload["phase"]
    plan_error = _validate_plan(db)
    if plan_error:
        return plan_error
    if phase == "super_reduce" and not _single_discount_completed(db):
        return _fail("five_price_single_discount_not_completed",
                     phase=phase)
    operation = OPERATION_BY_PHASE[phase]
    scope_sha = _scope_sha(phase)
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == operation,
        CampaignExecutionAttempt.scope_sha256 == scope_sha,
    ).with_for_update()).scalar_one_or_none()
    if existing:
        return _fail("five_price_attempt_already_consumed_no_retry",
                     phase=phase, attempt_id=existing.id,
                     attempt_state=existing.state,
                     platform_write=existing.platform_write_observed)
    attempt_id = secrets.token_hex(12)
    attempt = CampaignExecutionAttempt(
        id=attempt_id, plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=operation, scope_sha256=scope_sha,
        state="write_claimed", write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc),
        platform_write_observed=False, automatic_retry_allowed=False,
        request_id=f"five-price-{phase}-{secrets.token_hex(6)}",
        last_step="exact_manifest_claimed",
        result_summary={"request": payload,
                        "zero_sales_excluded_item_id": ZERO_SALES_EXCLUDED_ITEM_ID})
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("five_price_claim_raced_no_write", phase=phase)
    try:
        terminal = web_agent_service.correct_five_price(
            db, payload=payload, timeout_s=2400)
    except Exception as exc:
        terminal = {"ok": False, "submitted": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "execution_boundary": {"platform_write": None}}
    observed = (terminal.get("execution_boundary") or {}).get("platform_write")
    if observed not in {True, False}:
        observed = None
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.platform_write_observed = observed
    attempt.web_agent_job_id = terminal.get("web_agent_job_id")
    exact = _terminal_exact(terminal, phase)
    attempt.state = "completed" if exact else (
        "failed" if observed is False else "unknown")
    attempt.last_step = "exact_editor_readback" if exact else "terminal_not_exact"
    attempt.error_code = None if exact else str(
        terminal.get("error") or "five_price_terminal_not_exact")[:128]
    attempt.result_summary = {
        **(attempt.result_summary or {}),
        "terminal_ok": terminal.get("ok"),
        "submitted": terminal.get("submitted"),
        "item_count": terminal.get("item_count"),
        "target_sku_count": terminal.get("target_sku_count"),
        "row_count": terminal.get("row_count"),
        "partial_success_item_ids": terminal.get("partial_success_item_ids"),
        "terminal_error": terminal.get("error"),
    }
    db.commit()
    if not exact:
        return _fail("five_price_terminal_not_exact_no_retry",
                     phase=phase, attempt_id=attempt_id,
                     platform_write=observed, terminal=terminal)
    return {
        "ok": True, "phase": phase, "attempt_id": attempt_id,
        "result": terminal,
        "execution_boundary": _boundary(
            platform_write=bool(observed), phase=phase),
    }


def recover_single_discount(db: Session, *, payload: dict) -> dict:
    """Permit one exact recovery after the proven NAS-to-Agent timeout.

    The original unknown attempt remains immutable.  A separate durable claim
    is written before contacting Web-Agent, so a second recovery is impossible
    even if this call later has an unknown platform outcome.
    """
    if not validate_recovery_request(payload):
        return _fail("five_price_recovery_request_not_allowed",
                     phase="single_discount")
    plan_error = _validate_plan(db)
    if plan_error:
        return plan_error
    failed = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == RECOVERY_FAILED_ATTEMPT_ID,
    ).with_for_update()).scalar_one_or_none()
    if failed is None:
        return _fail("five_price_recovery_failed_attempt_not_found",
                     phase="single_discount")
    if not _write_free_timeout_attempt_exact(failed):
        return _fail("five_price_recovery_failed_attempt_not_write_free",
                     phase="single_discount")

    scope_sha = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == RECOVERY_OPERATION,
        CampaignExecutionAttempt.scope_sha256 == scope_sha,
    ).with_for_update()).scalar_one_or_none()
    if existing:
        return _fail("five_price_recovery_already_consumed_no_retry",
                     phase="single_discount", attempt_id=existing.id,
                     attempt_state=existing.state,
                     platform_write=existing.platform_write_observed)

    attempt_id = secrets.token_hex(12)
    attempt = CampaignExecutionAttempt(
        id=attempt_id, plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=RECOVERY_OPERATION, scope_sha256=scope_sha,
        state="write_claimed", write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc),
        platform_write_observed=False, automatic_retry_allowed=False,
        request_id=f"five-price-recover-{secrets.token_hex(6)}",
        last_step="exact_write_free_timeout_recovery_claimed",
        result_summary={
            "request": payload,
            "failed_attempt_id": RECOVERY_FAILED_ATTEMPT_ID,
            "failed_attempt_preserved": True,
            "zero_sales_excluded_item_id": ZERO_SALES_EXCLUDED_ITEM_ID,
        })
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("five_price_recovery_claim_raced_no_write",
                     phase="single_discount")

    try:
        terminal = web_agent_service.correct_five_price(
            db, payload=request_payload("single_discount"), timeout_s=2400)
    except Exception as exc:
        terminal = {
            "ok": False, "submitted": None,
            "error": f"{type(exc).__name__}: {exc}",
            "execution_boundary": {"platform_write": None},
        }
    observed = (terminal.get("execution_boundary") or {}).get(
        "platform_write")
    if observed not in {True, False}:
        observed = None
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.platform_write_observed = observed
    attempt.web_agent_job_id = terminal.get("web_agent_job_id")
    exact = _terminal_exact(terminal, "single_discount")
    attempt.state = "completed" if exact else (
        "failed" if observed is False else "unknown")
    attempt.last_step = (
        "exact_editor_readback" if exact else "recovery_terminal_not_exact")
    attempt.error_code = None if exact else str(
        terminal.get("error") or "five_price_recovery_terminal_not_exact")[:128]
    attempt.result_summary = {
        **(attempt.result_summary or {}),
        "terminal_ok": terminal.get("ok"),
        "submitted": terminal.get("submitted"),
        "row_count": terminal.get("row_count"),
        "terminal_error": terminal.get("error"),
    }
    db.commit()
    if not exact:
        return _fail("five_price_recovery_terminal_not_exact_no_retry",
                     phase="single_discount", attempt_id=attempt_id,
                     platform_write=observed, terminal=terminal)
    return {
        "ok": True, "phase": "single_discount",
        "recovered_failed_attempt_id": RECOVERY_FAILED_ATTEMPT_ID,
        "attempt_id": attempt_id, "result": terminal,
        "execution_boundary": _boundary(
            platform_write=bool(observed), phase="single_discount"),
    }

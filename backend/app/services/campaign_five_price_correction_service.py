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
SUPER_RECOVERY_FAILED_ATTEMPT_ID = "5373510a5a33b115e4771f37"
SUPER_RECOVERY_OPERATION = "five_price_recover_super"
SUPER_RECOVERY_V2_FAILED_ATTEMPT_ID = "b3f1282a5b34b0181d1daa95"
SUPER_RECOVERY_V2_OPERATION = "five_price_recover_super_v2"
ZERO_SALES_EXCLUDED_ITEM_ID = "793202812082"
SUPER_RECOVERY_V2_OLD_TARGETS = {
    "1046992283533": {"6241476755540": "388.00"},
    "717418169535": {"5011017605466": "400.00"},
    "840643621692": {
        "5606206268612": "277.00",
        "5917906151868": "277.00",
    },
    "840659847455": {
        "5917936191346": "500.00",
        "5777849039084": "500.00",
        "5917936191344": "500.00",
        "5917936191345": "500.00",
    },
}


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


def super_recovery_request_payload() -> dict:
    return {
        **request_payload("super_reduce"),
        "expected_failed_attempt_id": SUPER_RECOVERY_FAILED_ATTEMPT_ID,
        "confirmed_no_platform_write": True,
    }


def validate_super_recovery_request(payload: dict) -> bool:
    return (
        isinstance(payload, dict)
        and payload == super_recovery_request_payload()
    )


def super_recovery_v2_request_payload() -> dict:
    return {
        **request_payload("super_reduce"),
        "expected_failed_attempt_id": SUPER_RECOVERY_V2_FAILED_ATTEMPT_ID,
        "confirmed_no_persisted_change": True,
    }


def validate_super_recovery_v2_request(payload: dict) -> bool:
    return (
        isinstance(payload, dict)
        and payload == super_recovery_v2_request_payload()
    )


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


def _write_free_super_locator_attempt_exact(
        attempt: CampaignExecutionAttempt) -> bool:
    summary = attempt.result_summary if isinstance(
        attempt.result_summary, dict) else {}
    terminal_error = str(summary.get("terminal_error") or "")
    expected_error = (
        "RuntimeError: 商品 1046992283533 指定 SKU 当前价变化: "
        "{'6241476755540': {'expected': '388.00', "
        "'actual': '6241476755540.00'}}"
    )
    return bool(
        attempt.id == SUPER_RECOVERY_FAILED_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID
        and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == OPERATION_BY_PHASE["super_reduce"]
        and attempt.scope_sha256 == _scope_sha("super_reduce")
        and attempt.state == "failed"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is False
        and attempt.web_agent_job_id == "job1"
        and attempt.automatic_retry_allowed is False
        and attempt.last_step == "terminal_not_exact"
        and str(attempt.error_code or "") == expected_error
        and summary.get("request") == request_payload("super_reduce")
        and summary.get("terminal_ok") is False
        and summary.get("submitted") is False
        and summary.get("partial_success_item_ids") == []
        and terminal_error == expected_error
    )


def _super_submit_control_attempt_exact(
        attempt: CampaignExecutionAttempt) -> bool:
    summary = attempt.result_summary if isinstance(
        attempt.result_summary, dict) else {}
    expected_error = "RuntimeError: 超级立减最终提交按钮不唯一或不存在"
    return bool(
        attempt.id == SUPER_RECOVERY_V2_FAILED_ATTEMPT_ID
        and attempt.plan_id == PLAN_ID
        and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == SUPER_RECOVERY_OPERATION
        and attempt.scope_sha256 == hashlib.sha256(json.dumps(
            super_recovery_request_payload(), sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        and attempt.state == "unknown"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is True
        and attempt.web_agent_job_id == "job1"
        and attempt.automatic_retry_allowed is False
        and attempt.last_step == "super_recovery_terminal_not_exact"
        and str(attempt.error_code or "") == expected_error
        and summary.get("request") == super_recovery_request_payload()
        and summary.get("failed_attempt_id") == SUPER_RECOVERY_FAILED_ATTEMPT_ID
        and summary.get("terminal_ok") is False
        and summary.get("submitted") is True
        and summary.get("partial_success_item_ids") == []
        and str(summary.get("terminal_error") or "") == expected_error
    )


def _money(value) -> str | None:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return None


def _readback_super_original_targets(db: Session) -> dict:
    evidence = []
    for item_id, targets in SUPER_RECOVERY_V2_OLD_TARGETS.items():
        result = web_agent_service.inspect_super_reduce_item(
            db, item_id=item_id, timeout_s=300)
        if not result.get("ok"):
            return _fail(
                "five_price_super_recovery_v2_readback_failed",
                phase="super_reduce", item_id=item_id,
                readback_error=result.get("error"),
                web_agent_job_id=result.get("web_agent_job_id"))
        if result.get("status") != "活动中":
            return _fail(
                "five_price_super_recovery_v2_item_not_active",
                phase="super_reduce", item_id=item_id,
                actual_status=result.get("status"))
        inventory = result.get("editor_inventory") or {}
        rows = inventory.get("rows") or []
        by_sku = {}
        duplicate_skus = set()
        for row in rows:
            sku_id = str(row.get("sku_id") or "")
            inputs = row.get("inputs") or []
            if sku_id in by_sku:
                duplicate_skus.add(sku_id)
            if len(inputs) == 1:
                by_sku[sku_id] = _money(inputs[0].get("value"))
        if duplicate_skus:
            return _fail(
                "five_price_super_recovery_v2_duplicate_sku",
                phase="super_reduce", item_id=item_id,
                duplicate_sku_ids=sorted(duplicate_skus))
        mismatch = {
            sku_id: {"expected": expected, "actual": by_sku.get(sku_id)}
            for sku_id, expected in targets.items()
            if by_sku.get(sku_id) != expected
        }
        if mismatch:
            return _fail(
                "five_price_super_recovery_v2_price_not_original",
                phase="super_reduce", item_id=item_id, mismatch=mismatch)
        evidence.append({
            "item_id": item_id,
            "web_agent_job_id": result.get("web_agent_job_id"),
            "target_prices": {sku: by_sku[sku] for sku in targets},
            "platform_write": False,
        })
    return {"ok": True, "items": evidence}


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


def recover_super_reduce(db: Session, *, payload: dict) -> dict:
    """Permit one exact recovery after the proven write-free locator defect."""
    if not validate_super_recovery_request(payload):
        return _fail("five_price_super_recovery_request_not_allowed",
                     phase="super_reduce")
    plan_error = _validate_plan(db)
    if plan_error:
        return plan_error
    if not _single_discount_completed(db):
        return _fail("five_price_single_discount_not_completed",
                     phase="super_reduce")
    failed = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == SUPER_RECOVERY_FAILED_ATTEMPT_ID,
    ).with_for_update()).scalar_one_or_none()
    if failed is None:
        return _fail("five_price_super_recovery_failed_attempt_not_found",
                     phase="super_reduce")
    if not _write_free_super_locator_attempt_exact(failed):
        return _fail("five_price_super_recovery_failed_attempt_not_write_free",
                     phase="super_reduce")

    scope_sha = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == SUPER_RECOVERY_OPERATION,
        CampaignExecutionAttempt.scope_sha256 == scope_sha,
    ).with_for_update()).scalar_one_or_none()
    if existing:
        return _fail("five_price_super_recovery_already_consumed_no_retry",
                     phase="super_reduce", attempt_id=existing.id,
                     attempt_state=existing.state,
                     platform_write=existing.platform_write_observed)

    attempt_id = secrets.token_hex(12)
    attempt = CampaignExecutionAttempt(
        id=attempt_id, plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=SUPER_RECOVERY_OPERATION, scope_sha256=scope_sha,
        state="write_claimed", write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc),
        platform_write_observed=False, automatic_retry_allowed=False,
        request_id=f"five-price-recover-super-{secrets.token_hex(6)}",
        last_step="exact_write_free_locator_recovery_claimed",
        result_summary={
            "request": payload,
            "failed_attempt_id": SUPER_RECOVERY_FAILED_ATTEMPT_ID,
            "failed_attempt_preserved": True,
            "zero_sales_excluded_item_id": ZERO_SALES_EXCLUDED_ITEM_ID,
        })
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("five_price_super_recovery_claim_raced_no_write",
                     phase="super_reduce")

    try:
        terminal = web_agent_service.correct_five_price(
            db, payload=request_payload("super_reduce"), timeout_s=2400)
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
    exact = _terminal_exact(terminal, "super_reduce")
    attempt.state = "completed" if exact else (
        "failed" if observed is False else "unknown")
    attempt.last_step = (
        "exact_editor_readback" if exact
        else "super_recovery_terminal_not_exact")
    attempt.error_code = None if exact else str(
        terminal.get("error")
        or "five_price_super_recovery_terminal_not_exact")[:128]
    attempt.result_summary = {
        **(attempt.result_summary or {}),
        "terminal_ok": terminal.get("ok"),
        "submitted": terminal.get("submitted"),
        "item_count": terminal.get("item_count"),
        "target_sku_count": terminal.get("target_sku_count"),
        "partial_success_item_ids": terminal.get("partial_success_item_ids"),
        "terminal_error": terminal.get("error"),
    }
    db.commit()
    if not exact:
        return _fail("five_price_super_recovery_terminal_not_exact_no_retry",
                     phase="super_reduce", attempt_id=attempt_id,
                     platform_write=observed, terminal=terminal)
    return {
        "ok": True, "phase": "super_reduce",
        "recovered_failed_attempt_id": SUPER_RECOVERY_FAILED_ATTEMPT_ID,
        "attempt_id": attempt_id, "result": terminal,
        "execution_boundary": _boundary(
            platform_write=bool(observed), phase="super_reduce"),
    }


def recover_super_reduce_v2(db: Session, *, payload: dict) -> dict:
    """Recover only after four fresh readbacks prove no price persisted."""
    if not validate_super_recovery_v2_request(payload):
        return _fail("five_price_super_recovery_v2_request_not_allowed",
                     phase="super_reduce")
    plan_error = _validate_plan(db)
    if plan_error:
        return plan_error
    if not _single_discount_completed(db):
        return _fail("five_price_single_discount_not_completed",
                     phase="super_reduce")
    failed = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == SUPER_RECOVERY_V2_FAILED_ATTEMPT_ID,
    ).with_for_update()).scalar_one_or_none()
    if failed is None:
        return _fail("five_price_super_recovery_v2_failed_attempt_not_found",
                     phase="super_reduce")
    if not _super_submit_control_attempt_exact(failed):
        return _fail(
            "five_price_super_recovery_v2_failed_attempt_not_exact",
            phase="super_reduce")

    scope_sha = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == SUPER_RECOVERY_V2_OPERATION,
        CampaignExecutionAttempt.scope_sha256 == scope_sha,
    ).with_for_update()).scalar_one_or_none()
    if existing:
        return _fail("five_price_super_recovery_v2_already_consumed_no_retry",
                     phase="super_reduce", attempt_id=existing.id,
                     attempt_state=existing.state,
                     platform_write=existing.platform_write_observed)

    # This platform read happens before the one-shot claim.  It must prove all
    # eight target values are still the fixed originals after the previous
    # next-step-only attempt; otherwise no recovery write is permitted.
    preflight = _readback_super_original_targets(db)
    if not preflight.get("ok"):
        return preflight

    attempt_id = secrets.token_hex(12)
    attempt = CampaignExecutionAttempt(
        id=attempt_id, plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=SUPER_RECOVERY_V2_OPERATION, scope_sha256=scope_sha,
        state="write_claimed", write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc),
        platform_write_observed=False, automatic_retry_allowed=False,
        request_id=f"five-price-recover-super-v2-{secrets.token_hex(6)}",
        last_step="four_item_original_price_readback_claimed",
        result_summary={
            "request": payload,
            "failed_attempt_id": SUPER_RECOVERY_V2_FAILED_ATTEMPT_ID,
            "failed_attempt_preserved": True,
            "preflight_readback": preflight,
            "zero_sales_excluded_item_id": ZERO_SALES_EXCLUDED_ITEM_ID,
        })
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("five_price_super_recovery_v2_claim_raced_no_write",
                     phase="super_reduce")

    try:
        terminal = web_agent_service.correct_five_price(
            db, payload=request_payload("super_reduce"), timeout_s=2400)
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
    exact = _terminal_exact(terminal, "super_reduce")
    attempt.state = "completed" if exact else (
        "failed" if observed is False else "unknown")
    attempt.last_step = (
        "exact_editor_readback" if exact
        else "super_recovery_v2_terminal_not_exact")
    attempt.error_code = None if exact else str(
        terminal.get("error")
        or "five_price_super_recovery_v2_terminal_not_exact")[:128]
    attempt.result_summary = {
        **(attempt.result_summary or {}),
        "terminal_ok": terminal.get("ok"),
        "submitted": terminal.get("submitted"),
        "item_count": terminal.get("item_count"),
        "target_sku_count": terminal.get("target_sku_count"),
        "partial_success_item_ids": terminal.get("partial_success_item_ids"),
        "terminal_error": terminal.get("error"),
    }
    db.commit()
    if not exact:
        return _fail(
            "five_price_super_recovery_v2_terminal_not_exact_no_retry",
            phase="super_reduce", attempt_id=attempt_id,
            platform_write=observed, terminal=terminal)
    return {
        "ok": True, "phase": "super_reduce",
        "recovered_failed_attempt_id": SUPER_RECOVERY_V2_FAILED_ATTEMPT_ID,
        "attempt_id": attempt_id, "preflight_readback": preflight,
        "result": terminal,
        "execution_boundary": _boundary(
            platform_write=bool(observed), phase="super_reduce"),
    }

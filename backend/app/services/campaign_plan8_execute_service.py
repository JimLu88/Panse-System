"""One-shot execution for the user-approved 2026-09 Super88 plan 8."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_execution_service,
    campaign_policy_service,
    campaign_service,
)


WORKFLOW_KEY = "campaign:super88:49462:49469"
PLAN_ID = 8
EXPECTED_STATUS = "alarmed"
EXPECTED_CANDIDATE_SHA256 = (
    "bddba1f579359389d85928c0ccff75b7e9595ac767504121de16b3c661560070"
)
EXPECTED_UNAVAILABLE_ITEM_IDS = {"793202812082"}
OPERATION = "plan8_discount_and_signup"


def _boundary(*, platform_write: bool = False) -> dict:
    return {
        "plan_scoped_only": True,
        "platform_read": True,
        "platform_write": platform_write,
        "price_change": False,
        "sku_rotation": False,
        "withdraw_pause_remove": False,
        "warehouse_item_write": False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, **detail) -> dict:
    return {"ok": False, "error": error, **detail,
            "execution_boundary": _boundary()}


def _scope_digest(*, identity: dict, signup_rows: list[dict],
                  discount_rows: list[dict], policy_sha256: str,
                  candidate_sha256: str) -> str:
    payload = {
        "identity": identity,
        "policy_sha256": policy_sha256,
        "candidate_sha256": candidate_sha256,
        "unavailable_item_ids": sorted(EXPECTED_UNAVAILABLE_ITEM_IDS),
        "signup": sorted((
            str(row.get("taobao_item_id") or ""),
            str(row.get("taobao_sku_id") or ""),
            str(row.get("price") or ""),
            bool(row.get("is_placeholder")),
        ) for row in signup_rows),
        "discount": sorted((
            str(row.get("taobao_item_id") or ""),
            str(row.get("taobao_sku_id") or ""),
            str(row.get("deduct") or ""),
            str(row.get("target_price") or ""),
        ) for row in discount_rows),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _attempt_replay(attempt: CampaignExecutionAttempt, plan: CampaignPlan) -> dict:
    if attempt.state == "completed":
        return {
            "ok": True,
            "idempotent_replay": True,
            "workflow_key": WORKFLOW_KEY,
            "plan_id": PLAN_ID,
            "plan_status": plan.status,
            "attempt_id": attempt.id,
            "scope_sha256": attempt.scope_sha256,
            "result": attempt.result_summary or {},
            "execution_boundary": _boundary(platform_write=True),
        }
    return _fail(
        "plan8_attempt_already_claimed_no_retry",
        attempt_id=attempt.id,
        attempt_state=attempt.state,
        plan_status=plan.status,
        platform_write_observed=attempt.platform_write_observed,
    )


def execute_plan8(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, expected_candidate_sha256: str) -> dict:
    """Claim once, push same-window discount, submit, then exact readback."""
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_status != EXPECTED_STATUS
            or expected_candidate_sha256 != EXPECTED_CANDIDATE_SHA256):
        return _fail("plan8_execute_request_not_allowed")

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    identity = campaign_service.campaign_identity(plan)
    if (not identity.get("ok")
            or identity.get("campaign_title") != "26年淘宝9月超级88"
            or identity.get("campaign_id") != "49462"
            or identity.get("united_activity_id") != "49469"
            or identity.get("sign_record_id") != "3527841611"
            or identity.get("campaign_start") != "2026-09-06 20:00:00"
            or identity.get("campaign_end") != "2026-09-13 23:59:59"
            or identity.get("official_rate") != "12%"):
        return _fail("plan8_identity_not_allowed", identity=identity)

    old_attempts = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
    )).scalars().all()
    if old_attempts:
        return _attempt_replay(old_attempts[0], plan)
    prior_signup_writes = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == "signup",
        CampaignExecutionAttempt.write_claimed.is_(True),
    )).scalars().all()
    if prior_signup_writes:
        return _fail(
            "prior_plan8_signup_write_blocks_execute",
            attempt_ids=[row.id for row in prior_signup_writes],
            attempt_states=[row.state for row in prior_signup_writes],
        )
    if plan.status != expected_status:
        return _fail(
            "plan8_status_cas_mismatch",
            expected_status=expected_status,
            actual_status=plan.status,
        )

    unavailable = campaign_service.candidate_unavailable_items_for_plan(db, plan)
    if set(unavailable) != EXPECTED_UNAVAILABLE_ITEM_IDS:
        return _fail(
            "plan8_candidate_unavailable_scope_mismatch",
            expected_item_ids=sorted(EXPECTED_UNAVAILABLE_ITEM_IDS),
            actual_item_ids=sorted(unavailable),
        )
    candidate_hashes = {
        str(row.get("evidence_sha256") or "") for row in unavailable.values()
    }
    if candidate_hashes != {expected_candidate_sha256}:
        return _fail(
            "plan8_candidate_evidence_changed",
            expected_candidate_sha256=expected_candidate_sha256,
            actual_candidate_sha256=sorted(candidate_hashes),
        )

    checks = campaign_service.preflight(db, plan)
    blocking = [row for row in checks if row.get("level") == "error"]
    if blocking:
        return _fail("plan8_preflight_blocked", blocking=blocking)
    signup_rows, signup_stats = campaign_service.build_signup_rows(db, plan)
    discount_rows, discount_stats = campaign_service.build_discount_rows(db, plan)
    signup_items = {str(row.get("taobao_item_id") or "") for row in signup_rows}
    discount_items = {str(row.get("taobao_item_id") or "") for row in discount_rows}
    if (not signup_rows or not discount_rows
            or EXPECTED_UNAVAILABLE_ITEM_IDS & signup_items
            or EXPECTED_UNAVAILABLE_ITEM_IDS & discount_items
            or "1038725569412" in signup_items
            or "1038725569412" in discount_items):
        return _fail(
            "plan8_scope_guard_failed",
            signup_items=sorted(signup_items),
            discount_items=sorted(discount_items),
            excluded_signup=signup_stats.get("excluded_whole_items") or [],
            excluded_discount=discount_stats.get("excluded_whole_items") or [],
        )
    policy = campaign_policy_service.require_policy()
    scope_sha = _scope_digest(
        identity=identity,
        signup_rows=signup_rows,
        discount_rows=discount_rows,
        policy_sha256=str(policy.get("_sha256") or ""),
        candidate_sha256=expected_candidate_sha256,
    )
    attempt = CampaignExecutionAttempt(
        id=secrets.token_hex(12),
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        operation=OPERATION,
        scope_sha256=scope_sha,
        state="prepared",
        write_claimed=False,
        automatic_retry_allowed=False,
        result_summary={
            "signup_rows": len(signup_rows),
            "discount_rows": len(discount_rows),
            "signup_items": sorted(signup_items),
            "discount_items": sorted(discount_items),
            "candidate_sha256": expected_candidate_sha256,
            "candidate_unavailable_items": sorted(unavailable),
        },
    )
    db.add(attempt)
    db.commit()
    request_id = f"plan8-execute-{secrets.token_hex(8)}"
    campaign_execution_service.claim_platform_write(
        db, attempt.id, request_id=request_id)

    try:
        discount = campaign_service.push_discount(db, plan, phase="commit")
        if not discount.get("ok"):
            plan.status = "alarmed"
            db.commit()
            summary = {
                "discount": discount,
                "signup": None,
                "scope_sha256": scope_sha,
            }
            campaign_execution_service.record_platform_terminal(
                db, attempt, state="failed_no_retry",
                platform_write_observed=bool(discount.get("submitted")),
                step=discount.get("step") or "plan8_discount",
                error_code=discount.get("error") or "plan8_discount_failed",
                job_id=discount.get("job"), result_summary=summary)
            return _fail(
                "plan8_discount_failed_no_retry",
                attempt_id=attempt.id, scope_sha256=scope_sha,
                result=summary, plan_status=plan.status)

        signup = campaign_service.push_signup(
            db, plan, execution_source="campaign_automation")
        succeeded = bool(signup.get("ok"))
        if not succeeded:
            plan.status = "alarmed"
            db.commit()
        summary = {
            "discount": discount,
            "signup": signup,
            "scope_sha256": scope_sha,
        }
        campaign_execution_service.record_platform_terminal(
            db, attempt,
            state="completed" if succeeded else "failed_no_retry",
            platform_write_observed=True,
            step=(signup.get("step") or
                  ("plan8_completed" if succeeded else "plan8_signup")),
            error_code=None if succeeded else (
                signup.get("error") or "plan8_signup_failed"),
            job_id=signup.get("job"), result_summary=summary)
        return {
            "ok": succeeded,
            "error": None if succeeded else "plan8_signup_failed_no_retry",
            "workflow_key": WORKFLOW_KEY,
            "plan_id": PLAN_ID,
            "plan_status": plan.status,
            "attempt_id": attempt.id,
            "scope_sha256": scope_sha,
            "result": summary,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "execution_boundary": _boundary(platform_write=True),
        }
    except Exception as exc:  # unknown outcome after the outer write claim
        db.rollback()
        plan = db.get(CampaignPlan, PLAN_ID)
        if plan is not None:
            plan.status = "alarmed"
            db.commit()
        attempt = db.get(CampaignExecutionAttempt, attempt.id)
        campaign_execution_service.record_platform_terminal(
            db, attempt, state="unknown_no_retry",
            platform_write_observed=None, step="plan8_execute_exception",
            error_code=type(exc).__name__,
            result_summary={"scope_sha256": scope_sha})
        return _fail(
            "plan8_execution_unknown_outcome_no_retry",
            attempt_id=attempt.id, scope_sha256=scope_sha,
            plan_status=getattr(plan, "status", None),
            error_type=type(exc).__name__)

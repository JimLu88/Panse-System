"""One-shot signup-only recovery for the partially executed Super88 plan 8.

The original outer attempt already completed the single-item discount write and
failed before the signup workbook was uploaded.  This service binds that exact
failure receipt, refreshes current official evidence without a write claim,
removes the one already-published in-scope item, and permits one upload for the
remaining six items only.  Any claimed recovery is permanently no-retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
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
OPERATION = "plan8_signup_recovery"

ORIGINAL_ATTEMPT_ID = "14ddfc8e428148b66f61c7aa"
ORIGINAL_OPERATION = "plan8_discount_and_signup"
ORIGINAL_OUTER_SCOPE_SHA256 = (
    "7c1f20ed3693bbef62b0cf53d1f3a16acf969e8c615952b61b4db24a6c83665f"
)
EXPECTED_FULL_SIGNUP_SCOPE_SHA256 = (
    "a08cf3892aecfac211b04bbce7761eac969b75814a72eeb1dece89a64e4dc5c5"
)
EXPECTED_PENDING_SCOPE_SHA256 = (
    "f60f4eda9a238702dc2b69cf0db61fd3ca0ded844cf6ccd3032650f56a663805"
)
EXPECTED_POLICY_SHA256 = (
    "66487550dd76974d415dd00e3b3153d6605a4b24bae6d314792e457501480076"
)
EXPECTED_CANDIDATE_SHA256 = (
    "bddba1f579359389d85928c0ccff75b7e9595ac767504121de16b3c661560070"
)
EXPECTED_UNAVAILABLE_ITEM_IDS = {"793202812082"}
EXPECTED_ALREADY_PUBLISHED_ITEM_IDS = {
    "1001358847694", "805268708396", "863525290377",
}
EXPECTED_PENDING_ITEM_IDS = {
    "1036279566778",
    "1036312802226",
    "1074244132390",
    "837902729785",
    "841201084787",
    "917179577721",
}
EXPECTED_FULL_ITEM_IDS = EXPECTED_PENDING_ITEM_IDS | {"805268708396"}
EXPECTED_OFFICIAL_RECORD_ITEM_IDS = (
    EXPECTED_ALREADY_PUBLISHED_ITEM_IDS | EXPECTED_PENDING_ITEM_IDS
)
EXPECTED_FULL_ROW_COUNT = 53
EXPECTED_PENDING_ROW_COUNT = 52


def _boundary(*, platform_write: bool = False) -> dict:
    return {
        "plan_scoped_only": True,
        "platform_read": True,
        "platform_write": platform_write,
        "price_change": False,
        "sku_rotation": False,
        "withdraw_pause_remove": False,
        "warehouse_item_write": False,
        "discount_write": False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, **detail) -> dict:
    return {
        "ok": False,
        "error": error,
        **detail,
        "execution_boundary": _boundary(),
    }


def _identity_allowed(plan: CampaignPlan) -> tuple[bool, dict]:
    identity = campaign_service.campaign_identity(plan)
    expected = {
        "campaign_title": "26年淘宝9月超级88",
        "campaign_id": "49462",
        "united_activity_id": "49469",
        "sign_record_id": "3527841611",
        "campaign_start": "2026-09-06 20:00:00",
        "campaign_end": "2026-09-13 23:59:59",
        "official_rate": "12%",
        "platform_activity_mode": "fixed_window",
    }
    return bool(identity.get("ok") and all(
        str(identity.get(key) or "") == value
        for key, value in expected.items()
    )), identity


def _original_attempt_allowed(attempt: CampaignExecutionAttempt | None) -> bool:
    if attempt is None:
        return False
    summary = attempt.result_summary or {}
    discount = summary.get("discount") or {}
    return bool(
        attempt.plan_id == PLAN_ID
        and attempt.workflow_key == WORKFLOW_KEY
        and attempt.operation == ORIGINAL_OPERATION
        and attempt.scope_sha256 == ORIGINAL_OUTER_SCOPE_SHA256
        and attempt.state == "failed_no_retry"
        and attempt.write_claimed is True
        and attempt.platform_write_observed is True
        and discount.get("ok") is True
        and discount.get("submitted") is True
        and summary.get("signup") is None
    )


def _scope_sha(identity: dict, rows: list[dict], policy_sha256: str) -> str:
    return campaign_execution_service.scope_sha256(
        identity=identity, rows=rows, policy_sha256=policy_sha256)


def _rows_scope_allowed(
        rows: list[dict], *, expected_items: set[str], expected_count: int) -> bool:
    items = {str(row.get("taobao_item_id") or "") for row in rows}
    sku_ids = [str(row.get("taobao_sku_id") or "") for row in rows]
    return bool(
        len(rows) == expected_count
        and items == expected_items
        and len(set(sku_ids)) == expected_count
        and all(value.isdigit() for value in sku_ids)
        and "1038725569412" not in items
        and "793202812082" not in items
    )


def validate_prepared_current_activity(current: dict) -> tuple[bool, dict]:
    """Validate the exact read-only export that will be reused by push_signup."""
    rows = current.get("rows") if isinstance(current, dict) else None
    export_evidence = (
        current.get("export_evidence") if isinstance(current, dict) else None)
    candidate = (
        current.get("candidate_evidence") if isinstance(current, dict) else None)
    unavailable = (
        current.get("candidate_unavailable") if isinstance(current, dict) else None)
    marketing = (
        export_evidence.get("marketing_records")
        if isinstance(export_evidence, dict) else None)
    identity = (
        export_evidence.get("identity")
        if isinstance(export_evidence, dict) else None)
    export_sha = str((export_evidence or {}).get("sha256") or "")

    active_items = {
        str(row.get("item_id") or "") for row in (rows or [])
        if str(row.get("item_id") or "")
    }
    record_items = {
        str(row.get("item_id") or "") for row in (marketing or [])
        if str(row.get("item_id") or "")
    }
    selected_enrolled = {
        str(row.get("item_id") or "") for row in (marketing or [])
        if row.get("selected") is True and row.get("proves_enrollment") is True
    }
    pending_selected = EXPECTED_PENDING_ITEM_IDS & selected_enrolled
    expected_identity = {
        "campaign_title": "26年淘宝9月超级88",
        "campaign_id": "49462",
        "united_activity_id": "49469",
        "sign_record_id": "3527841611",
        "campaign_start": "2026-09-06 20:00:00",
        "campaign_end": "2026-09-13 23:59:59",
        "official_rate": "12%",
        "platform_activity_mode": "fixed_window",
    }
    ok = bool(
        isinstance(current, dict) and current.get("ok") is True
        and isinstance(rows, list) and rows
        and isinstance(export_evidence, dict)
        and re.fullmatch(r"[0-9a-f]{64}", export_sha)
        and isinstance(identity, dict)
        and all(str(identity.get(key) or "") == value
                for key, value in expected_identity.items())
        and active_items == EXPECTED_ALREADY_PUBLISHED_ITEM_IDS
        and isinstance(marketing, list)
        and record_items == EXPECTED_OFFICIAL_RECORD_ITEM_IDS
        and selected_enrolled == EXPECTED_ALREADY_PUBLISHED_ITEM_IDS
        and not pending_selected
        and isinstance(candidate, dict)
        and candidate.get("sha256") == EXPECTED_CANDIDATE_SHA256
        and candidate.get("requested_sku_count") == 179
        and candidate.get("observed_sku_count") == 171
        and len(candidate.get("missing_sku_ids") or []) == 8
        and candidate.get("candidate_items_scanned") == 50
        and candidate.get("page_count") == 6
        and isinstance(unavailable, dict)
        and unavailable.get("complete") is True
        and set(unavailable.get("items") or [])
        == EXPECTED_UNAVAILABLE_ITEM_IDS
        and not (unavailable.get("partial_missing_items") or [])
        and unavailable.get("sha256") == EXPECTED_CANDIDATE_SHA256
    )
    detail = {
        "export_sha256": export_sha or None,
        "active_item_ids": sorted(active_items),
        "marketing_record_item_ids": sorted(record_items),
        "selected_enrolled_item_ids": sorted(selected_enrolled),
        "candidate_sha256": (candidate or {}).get("sha256"),
        "candidate_requested_skus": (candidate or {}).get("requested_sku_count"),
        "candidate_observed_skus": (candidate or {}).get("observed_sku_count"),
        "candidate_missing_skus": len((candidate or {}).get("missing_sku_ids") or []),
        "candidate_items_scanned": (candidate or {}).get("candidate_items_scanned"),
        "candidate_page_count": (candidate or {}).get("page_count"),
        "candidate_unavailable_item_ids": sorted(
            (unavailable or {}).get("items") or []),
    }
    return ok, detail


def _replay(attempt: CampaignExecutionAttempt, plan: CampaignPlan) -> dict:
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
        "plan8_signup_recovery_already_claimed_no_retry",
        attempt_id=attempt.id,
        attempt_state=attempt.state,
        platform_write_observed=attempt.platform_write_observed,
        plan_status=plan.status,
    )


def recover_plan8_signup(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, expected_original_attempt_id: str,
        expected_original_scope_sha256: str,
        expected_full_signup_scope_sha256: str,
        expected_pending_scope_sha256: str,
        expected_policy_sha256: str,
        expected_candidate_sha256: str) -> dict:
    """Perform one read-first, signup-only recovery for the exact six items."""
    if (
        workflow_key != WORKFLOW_KEY
        or expected_plan_id != PLAN_ID
        or expected_status != EXPECTED_STATUS
        or expected_original_attempt_id != ORIGINAL_ATTEMPT_ID
        or expected_original_scope_sha256 != ORIGINAL_OUTER_SCOPE_SHA256
        or expected_full_signup_scope_sha256 != EXPECTED_FULL_SIGNUP_SCOPE_SHA256
        or expected_pending_scope_sha256 != EXPECTED_PENDING_SCOPE_SHA256
        or expected_policy_sha256 != EXPECTED_POLICY_SHA256
        or expected_candidate_sha256 != EXPECTED_CANDIDATE_SHA256
    ):
        return _fail("plan8_signup_recovery_request_not_allowed")

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    identity_ok, identity = _identity_allowed(plan)
    if not identity_ok:
        return _fail("plan8_signup_recovery_identity_not_allowed", identity=identity)

    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
        CampaignExecutionAttempt.scope_sha256 == EXPECTED_PENDING_SCOPE_SHA256,
    )).scalar_one_or_none()
    if existing is not None:
        return _replay(existing, plan)

    original = db.get(CampaignExecutionAttempt, ORIGINAL_ATTEMPT_ID)
    if not _original_attempt_allowed(original):
        return _fail(
            "plan8_original_failure_receipt_mismatch",
            attempt_id=getattr(original, "id", None),
            attempt_state=getattr(original, "state", None),
            attempt_scope_sha256=getattr(original, "scope_sha256", None),
        )
    if plan.status != EXPECTED_STATUS:
        return _fail(
            "plan8_signup_recovery_status_cas_mismatch",
            expected_status=EXPECTED_STATUS,
            actual_status=plan.status,
        )

    try:
        policy = campaign_policy_service.require_policy()
    except Exception as exc:  # policy is an immutable pre-write dependency
        return _fail(
            "plan8_signup_recovery_policy_unavailable",
            error_type=type(exc).__name__)
    policy_sha = str(policy.get("_sha256") or "")
    if policy_sha != EXPECTED_POLICY_SHA256:
        return _fail(
            "plan8_signup_recovery_policy_changed",
            expected_policy_sha256=EXPECTED_POLICY_SHA256,
            actual_policy_sha256=policy_sha,
        )

    unavailable = campaign_service.candidate_unavailable_items_for_plan(db, plan)
    if (
        set(unavailable) != EXPECTED_UNAVAILABLE_ITEM_IDS
        or {str(row.get("evidence_sha256") or "") for row in unavailable.values()}
        != {EXPECTED_CANDIDATE_SHA256}
    ):
        return _fail(
            "plan8_signup_recovery_candidate_state_mismatch",
            unavailable_item_ids=sorted(unavailable),
            evidence_sha256=sorted({
                str(row.get("evidence_sha256") or "")
                for row in unavailable.values()}),
        )

    full_rows, full_stats = campaign_service.build_signup_rows(db, plan)
    full_scope_sha = _scope_sha(identity, full_rows, policy_sha)
    if (
        not _rows_scope_allowed(
            full_rows, expected_items=EXPECTED_FULL_ITEM_IDS,
            expected_count=EXPECTED_FULL_ROW_COUNT)
        or full_scope_sha != EXPECTED_FULL_SIGNUP_SCOPE_SHA256
    ):
        return _fail(
            "plan8_signup_recovery_full_scope_drift",
            expected_scope_sha256=EXPECTED_FULL_SIGNUP_SCOPE_SHA256,
            actual_scope_sha256=full_scope_sha,
            row_count=len(full_rows),
            item_ids=sorted({str(row.get("taobao_item_id") or "")
                             for row in full_rows}),
            stats=full_stats,
        )

    # This is intentionally before both the recovery attempt and write claim.
    # It is the only current-state export reused by the actual signup call.
    current = campaign_service.refresh_floor_evidence_from_current_activity(db, plan)
    if not current.get("ok"):
        return _fail(
            "plan8_signup_recovery_readonly_refresh_failed",
            step=current.get("step"),
            detail=current.get("detail"),
            job_id=current.get("job_id"),
            platform_error=current.get("error"),
        )
    current_ok, current_detail = validate_prepared_current_activity(current)
    if not current_ok:
        return _fail(
            "plan8_signup_recovery_current_activity_mismatch",
            current_activity=current_detail,
        )

    # Refresh can update exclusions/evidence and commit. Re-lock and repeat all
    # mutable CAS checks before creating a durable recovery attempt.
    db.expire_all()
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None or plan.status != EXPECTED_STATUS:
        return _fail(
            "plan8_signup_recovery_status_changed_after_refresh",
            actual_status=getattr(plan, "status", None),
        )
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
        CampaignExecutionAttempt.scope_sha256 == EXPECTED_PENDING_SCOPE_SHA256,
    )).scalar_one_or_none()
    if existing is not None:
        return _replay(existing, plan)
    original = db.get(CampaignExecutionAttempt, ORIGINAL_ATTEMPT_ID)
    if not _original_attempt_allowed(original):
        return _fail("plan8_original_failure_receipt_changed_after_refresh")

    refreshed_rows, refreshed_stats = campaign_service.build_signup_rows(db, plan)
    refreshed_scope = _scope_sha(identity, refreshed_rows, policy_sha)
    if (
        not _rows_scope_allowed(
            refreshed_rows, expected_items=EXPECTED_FULL_ITEM_IDS,
            expected_count=EXPECTED_FULL_ROW_COUNT)
        or refreshed_scope != EXPECTED_FULL_SIGNUP_SCOPE_SHA256
    ):
        return _fail(
            "plan8_signup_recovery_scope_changed_after_refresh",
            actual_scope_sha256=refreshed_scope,
            row_count=len(refreshed_rows),
            stats=refreshed_stats,
        )

    pending_rows = [
        row for row in refreshed_rows
        if str(row.get("taobao_item_id") or "") in EXPECTED_PENDING_ITEM_IDS
    ]
    pending_scope_sha = _scope_sha(identity, pending_rows, policy_sha)
    if (
        not _rows_scope_allowed(
            pending_rows, expected_items=EXPECTED_PENDING_ITEM_IDS,
            expected_count=EXPECTED_PENDING_ROW_COUNT)
        or pending_scope_sha != EXPECTED_PENDING_SCOPE_SHA256
    ):
        return _fail(
            "plan8_signup_recovery_pending_scope_drift",
            expected_scope_sha256=EXPECTED_PENDING_SCOPE_SHA256,
            actual_scope_sha256=pending_scope_sha,
            row_count=len(pending_rows),
            item_ids=sorted({str(row.get("taobao_item_id") or "")
                             for row in pending_rows}),
        )

    checks = campaign_service.preflight(
        db, plan, exact_item_scope=EXPECTED_PENDING_ITEM_IDS)
    by_rule = {str(row.get("rule") or ""): row for row in checks}
    blocking = [row for row in checks if row.get("level") == "error"]
    if (blocking or by_rule.get("R16", {}).get("level") != "pass"
            or by_rule.get("R17", {}).get("level") != "pass"):
        return _fail(
            "plan8_signup_recovery_preflight_blocked",
            blocking=blocking,
            gate_results={"R16": by_rule.get("R16"), "R17": by_rule.get("R17")},
        )

    official_identity = campaign_service._refresh_official_product_sku_identity(
        db, pending_rows)
    if (
        not official_identity.get("ok")
        or official_identity.get("checked_items") != 6
        or official_identity.get("checked_skus") != EXPECTED_PENDING_ROW_COUNT
    ):
        return _fail(
            "plan8_signup_recovery_official_sku_identity_blocked",
            official_product_sku_identity=official_identity,
        )

    prior_pending_signup = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == "signup",
        CampaignExecutionAttempt.scope_sha256 == EXPECTED_PENDING_SCOPE_SHA256,
    )).scalar_one_or_none()
    if prior_pending_signup is not None:
        return _fail(
            "plan8_pending_signup_attempt_already_exists_no_retry",
            signup_attempt_id=prior_pending_signup.id,
            signup_attempt_state=prior_pending_signup.state,
            signup_write_claimed=prior_pending_signup.write_claimed,
        )

    attempt = CampaignExecutionAttempt(
        id=secrets.token_hex(12),
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        operation=OPERATION,
        scope_sha256=EXPECTED_PENDING_SCOPE_SHA256,
        state="prepared",
        write_claimed=False,
        automatic_retry_allowed=False,
        result_summary={
            "original_attempt_id": ORIGINAL_ATTEMPT_ID,
            "original_outer_scope_sha256": ORIGINAL_OUTER_SCOPE_SHA256,
            "full_signup_scope_sha256": EXPECTED_FULL_SIGNUP_SCOPE_SHA256,
            "pending_signup_scope_sha256": EXPECTED_PENDING_SCOPE_SHA256,
            "policy_sha256": EXPECTED_POLICY_SHA256,
            "candidate_sha256": EXPECTED_CANDIDATE_SHA256,
            "pending_item_ids": sorted(EXPECTED_PENDING_ITEM_IDS),
            "pending_rows": EXPECTED_PENDING_ROW_COUNT,
            "pre_submit_export_sha256": current_detail["export_sha256"],
            "pre_submit_export_job_id": (
                current.get("export_evidence") or {}).get("job_id"),
        },
    )
    plan.status = "resume_executing"
    db.add(attempt)
    db.commit()
    request_id = f"plan8-signup-recovery-{secrets.token_hex(8)}"
    campaign_execution_service.claim_platform_write(
        db, attempt.id, request_id=request_id)

    try:
        result = campaign_service.push_signup(
            db,
            plan,
            execution_source="campaign_super88_plan8_signup_recovery",
            reuse_fresh_plan_evidence=True,
            exact_item_scope=EXPECTED_PENDING_ITEM_IDS,
            allow_terminal_no_sales_fallback=False,
            prepared_current_activity=current,
            prepared_official_product_identity=official_identity,
        )
    except Exception as exc:  # unknown after recovery write claim: never retry
        db.rollback()
        plan = db.get(CampaignPlan, PLAN_ID)
        if plan is not None and plan.status == "resume_executing":
            plan.status = "alarmed"
            db.commit()
        attempt = db.get(CampaignExecutionAttempt, attempt.id)
        campaign_execution_service.record_platform_terminal(
            db, attempt, state="unknown_no_retry",
            platform_write_observed=None,
            step="plan8_signup_recovery_exception",
            error_code=type(exc).__name__,
            result_summary={
                "scope_sha256": EXPECTED_PENDING_SCOPE_SHA256,
                "error_type": type(exc).__name__,
            })
        return _fail(
            "plan8_signup_recovery_unknown_outcome_no_retry",
            attempt_id=attempt.id,
            scope_sha256=EXPECTED_PENDING_SCOPE_SHA256,
            plan_status=getattr(plan, "status", None),
            error_type=type(exc).__name__,
        )

    completed = bool(result.get("ok"))
    submitted = bool(result.get("submitted"))
    plan = db.get(CampaignPlan, PLAN_ID)
    if not completed and plan is not None and plan.status == "resume_executing":
        plan.status = "alarmed"
        db.commit()
    summary = {
        "original_attempt_id": ORIGINAL_ATTEMPT_ID,
        "full_signup_scope_sha256": EXPECTED_FULL_SIGNUP_SCOPE_SHA256,
        "pending_signup_scope_sha256": EXPECTED_PENDING_SCOPE_SHA256,
        "pre_submit_current_activity": current_detail,
        "result": result,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    campaign_execution_service.record_platform_terminal(
        db, attempt,
        state="completed" if completed else "failed_no_retry",
        platform_write_observed=submitted,
        step=str(result.get("step") or (
            "completed" if completed else "plan8_signup_recovery_failed")),
        error_code=(None if completed else str(
            result.get("error") or "plan8_signup_recovery_failed")),
        job_id=str(result.get("job") or result.get("job_id") or "") or None,
        result_summary=summary,
    )
    response = {
        "ok": completed,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "plan_status": getattr(plan, "status", None),
        "attempt_id": attempt.id,
        "signup_attempt_id": (result.get("stats") or {}).get(
            "execution_attempt_id"),
        "scope_sha256": EXPECTED_PENDING_SCOPE_SHA256,
        "pre_submit_export_sha256": current_detail["export_sha256"],
        "result": result,
        "execution_boundary": _boundary(platform_write=submitted),
    }
    if not completed:
        response["error"] = (
            result.get("error") or "plan8_signup_recovery_failed_no_retry")
    return response

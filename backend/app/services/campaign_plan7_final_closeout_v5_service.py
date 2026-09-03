"""One-shot recovery for the V4 pre-write bundle-consumption defect.

V5 accepts only the exact production residue created by the sole V4 call.  It
temporarily releases that bundle inside the same audited execution, delegates
to the unchanged V4 signup guards, and consumes it again before returning.
No V4 invocation is replayed and no new evidence is fabricated.
"""
from __future__ import annotations

from datetime import datetime, timezone
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import (
    CampaignExecutionAttempt,
    CampaignPlan,
    CampaignPreparationBundle,
)
from app.services import (
    campaign_plan7_final_closeout_v4_service as v4,
    campaign_policy_service,
    campaign_service,
)


WORKFLOW_KEY = v4.WORKFLOW_KEY
PLAN_ID = v4.PLAN_ID
EXPECTED_PLAN_STATUS = "resume_executing"
FAILED_V4_INVOCATION_ID = "a531725a704ce1a910ddc008"
PREPARED_ATTEMPT_ID = "c7df358081734428cbf05cea"
PREPARED_BUNDLE_ID = "d7c563f9a793233e1ceab7b4"
PREPARED_BUNDLE_SOURCE_SHA256 = (
    "c148d01cd73c9afd62f8008e40b16f4f5755c88b57b4a47e363f59209c2138b4"
)
PREPARED_BUNDLE_MANIFEST_SHA256 = (
    "9d287a19027c45754c0cf8860046ca8184be7e290ac5f76ed4153928aae76d33"
)
FAILED_V4_REQUEST_ID = "3cf6aa79e038"
FAILED_V4_RECEIPT_SHA256 = (
    "ca8259c1b7dc237553ecbc87b630708739d670a9fd050e1082b4ce9120987963"
)
RECOVERY_ID = "plan7-final-closeout-v4-bundle-consumption-v5"
INVOCATION_OPERATION = "plan7_closeout_v5"


def _boundary(*, platform_write: bool | None = False) -> dict:
    return {
        "plan_scoped_only": True,
        "bundle_scoped_only": True,
        "platform_read": True,
        "platform_write": platform_write,
        "account_action": bool(platform_write),
        "price_change": False,
        "sku_rotation": False,
        "withdraw_pause_remove": False,
        "touches_plan8": False,
        "notification": platform_write is not False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, **detail) -> dict:
    return {
        "ok": False, "error": error, **detail,
        "execution_boundary": _boundary(),
    }


def request_payload() -> dict:
    return {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "expected_plan_status": EXPECTED_PLAN_STATUS,
        "failed_v4_invocation_id": FAILED_V4_INVOCATION_ID,
        "prepared_attempt_id": PREPARED_ATTEMPT_ID,
        "prepared_bundle_id": PREPARED_BUNDLE_ID,
        "expected_bundle_source_sha256": PREPARED_BUNDLE_SOURCE_SHA256,
        "expected_bundle_manifest_sha256": PREPARED_BUNDLE_MANIFEST_SHA256,
        "failed_v4_request_id": FAILED_V4_REQUEST_ID,
        "failed_v4_receipt_sha256": FAILED_V4_RECEIPT_SHA256,
        "recovery_id": RECOVERY_ID,
    }


def validate_request(payload: dict) -> bool:
    return isinstance(payload, dict) and payload == request_payload()


INVOCATION_SCOPE_SHA256 = v4.v3._canonical_sha256(request_payload())


def _claim_invocation(db: Session) -> tuple[CampaignExecutionAttempt, bool]:
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == INVOCATION_OPERATION,
        CampaignExecutionAttempt.scope_sha256 == INVOCATION_SCOPE_SHA256,
    ).with_for_update()).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = CampaignExecutionAttempt(
        id=secrets.token_hex(12), plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY, operation=INVOCATION_OPERATION,
        scope_sha256=INVOCATION_SCOPE_SHA256, state="prepared",
        write_claimed=False, automatic_retry_allowed=False,
        result_summary={
            "recovery_id": RECOVERY_ID,
            "failed_v4_invocation_id": FAILED_V4_INVOCATION_ID,
            "prepared_attempt_id": PREPARED_ATTEMPT_ID,
            "prepared_bundle_id": PREPARED_BUNDLE_ID,
            "execution_boundary": _boundary(),
        },
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.execute(select(CampaignExecutionAttempt).where(
            CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
            CampaignExecutionAttempt.operation == INVOCATION_OPERATION,
            CampaignExecutionAttempt.scope_sha256 == INVOCATION_SCOPE_SHA256,
        )).scalar_one()
        return row, False
    return row, True


def _finish(db: Session, invocation_id: str, *, state: str,
            result: dict) -> None:
    row = db.get(CampaignExecutionAttempt, invocation_id)
    if row is None:
        raise RuntimeError("final_closeout_v5_invocation_missing")
    row.state = state
    row.last_step = str(result.get("step") or state)[:64]
    row.error_code = str(result.get("error") or "")[:128] or None
    row.automatic_retry_allowed = False
    row.result_summary = result
    db.commit()


def _residue_error(
        plan: CampaignPlan | None,
        failed_v4: CampaignExecutionAttempt | None,
        attempt: CampaignExecutionAttempt | None,
        bundle: CampaignPreparationBundle | None,
) -> str | None:
    if plan is None or (
            plan.id != PLAN_ID or plan.workflow_key != WORKFLOW_KEY
            or plan.status != EXPECTED_PLAN_STATUS
            or plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or str(plan.qn_campaign_title or "").strip() != "超级立减"):
        return "final_closeout_v5_plan_residue_mismatch"
    if failed_v4 is None or (
            failed_v4.id != FAILED_V4_INVOCATION_ID
            or failed_v4.operation != v4.INVOCATION_OPERATION
            or failed_v4.state != "failed_no_retry"
            or failed_v4.write_claimed
            or failed_v4.platform_write_observed is not None
            or failed_v4.automatic_retry_allowed
            or failed_v4.error_code
            != "计划7最终收口 V4 上下文不完整，拒绝进入平台写入"):
        return "final_closeout_v5_failed_v4_residue_mismatch"
    failed_result = (
        failed_v4.result_summary if isinstance(failed_v4.result_summary, dict)
        else {})
    inner = failed_result.get("result") or {}
    detail = inner.get("detail") or {}
    if (failed_result.get("bundle_id") != PREPARED_BUNDLE_ID
            or failed_result.get("attempt_id") != PREPARED_ATTEMPT_ID
            or inner.get("step") != "plan7_final_closeout_v4_policy_guard"
            or detail.get("error") != "bundle_already_consumed"
            or detail.get("bundle_id") != PREPARED_BUNDLE_ID):
        return "final_closeout_v5_failed_v4_receipt_mismatch"
    if attempt is None or (
            attempt.id != PREPARED_ATTEMPT_ID
            or attempt.operation != "signup"
            or attempt.state != "prepared"
            or attempt.write_claimed
            or attempt.platform_write_observed is not None
            or attempt.automatic_retry_allowed):
        return "final_closeout_v5_prepared_attempt_residue_mismatch"
    summary = attempt.result_summary if isinstance(attempt.result_summary, dict) else {}
    identity = summary.get("official_product_sku_identity") or {}
    if (summary.get("prepared_bundle_id") != PREPARED_BUNDLE_ID
            or summary.get("source_bundle_id") != v4.SOURCE_BUNDLE_ID
            or summary.get("official_export_sha256") != v4.OFFICIAL_EXPORT_SHA256
            or summary.get("signup_rows") != v4.EXPECTED_SIGNUP_ROWS
            or summary.get("discount_rows_verified") != v4.EXPECTED_DISCOUNT_ROWS
            or summary.get("invocation_id") != FAILED_V4_INVOCATION_ID
            or not identity.get("ok")
            or identity.get("checked_items") != 1
            or identity.get("checked_skus") != v4.EXPECTED_SIGNUP_ROWS
            or identity.get("official_skus") != v4.EXPECTED_SIGNUP_ROWS
            or (identity.get("artifact") or {}).get("sha256")
            != v4.OFFICIAL_EXPORT_SHA256):
        return "final_closeout_v5_prepared_attempt_evidence_mismatch"
    if bundle is None or (
            bundle.id != PREPARED_BUNDLE_ID
            or bundle.plan_id != PLAN_ID or bundle.workflow_key != WORKFLOW_KEY
            or bundle.state != "ready_for_final_submission"
            or bundle.consumed_attempt_id != PREPARED_ATTEMPT_ID
            or bundle.consumed_at is None
            or bundle.source_sha256 != PREPARED_BUNDLE_SOURCE_SHA256
            or bundle.policy_sha256 != v4.POLICY_SHA256
            or bundle.manifest_sha256 != PREPARED_BUNDLE_MANIFEST_SHA256
            or len(bundle.signup_rows or []) != v4.EXPECTED_SIGNUP_ROWS
            or len(bundle.discount_rows or []) != v4.EXPECTED_DISCOUNT_ROWS):
        return "final_closeout_v5_consumed_bundle_residue_mismatch"
    return None


def execute_plan7_final_closeout_v5(db: Session, payload: dict) -> dict:
    if not validate_request(payload):
        return _fail("final_closeout_v5_request_not_allowed")
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID).with_for_update()).scalar_one_or_none()
    failed_v4 = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == FAILED_V4_INVOCATION_ID
    ).with_for_update()).scalar_one_or_none()
    attempt = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == PREPARED_ATTEMPT_ID
    ).with_for_update()).scalar_one_or_none()
    bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.id == PREPARED_BUNDLE_ID
    ).with_for_update()).scalar_one_or_none()
    error = _residue_error(plan, failed_v4, attempt, bundle)
    if error:
        return _fail(error)
    invocation, created = _claim_invocation(db)
    if not created:
        return _fail(
            "final_closeout_v5_already_invoked_no_retry",
            invocation_id=invocation.id, invocation_state=invocation.state)

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID).with_for_update()).scalar_one()
    failed_v4 = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == FAILED_V4_INVOCATION_ID
    ).with_for_update()).scalar_one()
    attempt = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == PREPARED_ATTEMPT_ID
    ).with_for_update()).scalar_one()
    bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.id == PREPARED_BUNDLE_ID
    ).with_for_update()).scalar_one()
    error = _residue_error(plan, failed_v4, attempt, bundle)
    if error:
        failure = _fail(error)
        _finish(db, invocation.id, state="blocked_prewrite", result=failure)
        return failure

    identity = dict((attempt.result_summary or {})[
        "official_product_sku_identity"])
    context = {
        "bundle_id": bundle.id,
        "source_sha256": bundle.source_sha256,
        "policy_sha256": bundle.policy_sha256,
        "manifest_sha256": bundle.manifest_sha256,
        "item_scope_sha256": v4.ITEM_SCOPE_SHA256,
    }
    bundle.consumed_at = None
    bundle.consumed_attempt_id = None
    db.flush()
    policy = campaign_policy_service.require_policy()
    prepared_ok, prepared_detail = v4.validate_push_context(
        db, plan, exact_item_scope={v4.TARGET_ITEM_ID},
        policy_sha256=str(policy.get("_sha256") or ""),
        prepared_bundle_context=context, official_identity=identity)
    if not prepared_ok:
        db.rollback()
        failure = _fail(
            "final_closeout_v5_released_bundle_context_mismatch",
            detail=prepared_detail)
        _finish(db, invocation.id, state="blocked_prewrite", result=failure)
        return failure

    try:
        result = campaign_service.push_signup(
            db, plan, execution_source=v4.EXECUTION_SOURCE,
            reuse_fresh_plan_evidence=True,
            exact_item_scope={v4.TARGET_ITEM_ID},
            allow_terminal_no_sales_fallback=False,
            prepared_official_product_identity=identity,
            prepared_bundle_context=context)
    except Exception as exc:  # noqa: BLE001 - claimed outcomes fail closed
        db.rollback()
        attempt = db.get(CampaignExecutionAttempt, PREPARED_ATTEMPT_ID)
        bundle = db.get(CampaignPreparationBundle, PREPARED_BUNDLE_ID)
        if bundle is not None:
            bundle.consumed_at = datetime.now(timezone.utc)
            bundle.consumed_attempt_id = PREPARED_ATTEMPT_ID
        plan = db.get(CampaignPlan, PLAN_ID)
        if plan is not None:
            plan.status = "alarmed"
        db.commit()
        observed = None if attempt is None else attempt.platform_write_observed
        failure = {
            "ok": False,
            "error": "final_closeout_v5_unknown_outcome_no_retry",
            "detail": f"{type(exc).__name__}: {exc}",
            "attempt_id": PREPARED_ATTEMPT_ID,
            "execution_boundary": _boundary(platform_write=observed),
        }
        _finish(db, invocation.id, state="unknown_no_retry", result=failure)
        return failure

    bundle = db.get(CampaignPreparationBundle, PREPARED_BUNDLE_ID)
    if bundle is not None:
        bundle.consumed_at = datetime.now(timezone.utc)
        bundle.consumed_attempt_id = PREPARED_ATTEMPT_ID
    db.commit()
    attempt = db.get(CampaignExecutionAttempt, PREPARED_ATTEMPT_ID)
    observed = None if attempt is None else attempt.platform_write_observed
    if not result.get("ok"):
        plan = db.get(CampaignPlan, PLAN_ID)
        if plan is not None and plan.status == EXPECTED_PLAN_STATUS:
            plan.status = "alarmed"
            db.commit()
        failure = {
            "ok": False,
            "error": result.get("error") or "final_closeout_v5_failed_no_retry",
            "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
            "bundle_id": PREPARED_BUNDLE_ID,
            "attempt_id": PREPARED_ATTEMPT_ID,
            "result": result,
            "execution_boundary": _boundary(platform_write=observed),
        }
        _finish(db, invocation.id, state="failed_no_retry", result=failure)
        return failure

    plan = db.get(CampaignPlan, PLAN_ID)
    plan.status = "reconciled"
    marker = (
        f"final_closeout_v5_bundle={PREPARED_BUNDLE_ID}; "
        f"final_closeout_v5_attempt={PREPARED_ATTEMPT_ID}"
    )
    if marker not in str(plan.remark or ""):
        plan.remark = f"{plan.remark or ''}; {marker}".strip("; ")
    db.commit()
    success = {
        "ok": True, "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
        "plan_status": plan.status, "bundle_id": PREPARED_BUNDLE_ID,
        "attempt_id": PREPARED_ATTEMPT_ID,
        "failed_v4_invocation_id": FAILED_V4_INVOCATION_ID,
        "submitted_item_ids": [v4.TARGET_ITEM_ID],
        "deferred_item_ids": sorted(v4.DEFERRED_ITEM_IDS),
        "preserved_active_item_ids": sorted(v4.PRESERVED_ACTIVE_ITEM_IDS),
        "exempt_item_ids": sorted(v4.EXEMPT_ITEM_IDS),
        "result": result,
        "execution_boundary": _boundary(platform_write=True),
    }
    _finish(db, invocation.id, state="completed", result=success)
    return success

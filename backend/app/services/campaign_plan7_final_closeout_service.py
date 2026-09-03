"""One-shot final closeout for the last safe Super Reduce plan-7 item.

The service is deliberately incident-specific.  It consumes one immutable
preparation bundle, refreshes the target item's complete official SKU identity
before the write claim, and then delegates the existing terminal/readback flow
to :func:`campaign_service.push_signup`.  Any claimed outcome is no-retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import (
    CampaignExecutionAttempt,
    CampaignPlan,
    CampaignPreparationBundle,
)
from app.services import (
    campaign_execution_service,
    campaign_policy_service,
    campaign_service,
)


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
EXPECTED_STATUS = "alarmed"
BUNDLE_ID = "f6dd4cb3b7ffea16178efd6b"
SOURCE_SHA256 = "7c1476c840526e459d5ec5755ca1072a676e5c40e77c71d779b15cb30c0ef58e"
POLICY_SHA256 = "dbb4a7294636fb2f5bfd115efd561976eb6684cbfc00b9ed2f0f4aa1850dfe33"
MANIFEST_SHA256 = "40337eb5781ce17a55c2787535e7137c6bde39fbdfb78a15676f6530322de013"
ITEM_SCOPE_SHA256 = "e6a3f59b93f5329a928263c976190cf707ef3c9db39c7654df6d1678f1d0c24e"
TARGET_ITEM_ID = "1036273574687"
DEFERRED_ITEM_IDS = {"1074244132390", "793202812082"}
PRESERVED_ACTIVE_ITEM_IDS = {"717809819543", "793084818113", "797294092429"}
EXEMPT_ITEM_IDS = {"805268708396"}
EXPECTED_SIGNUP_ROWS = 13
EXPECTED_DISCOUNT_ROWS = 9
EXECUTION_SOURCE = "campaign_super_reduce_plan7_final_closeout"
RECOVERY_ID = "plan7-final-closeout-product-export-claim-v3"
EXPECTED_WEB_AGENT_COMMIT = "c7fdea3ed4594983d8f8baea896ff8e65088f2b8"


def _canonical_sha256(value) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _boundary(*, platform_read: bool = False,
              platform_write: bool | None = False) -> dict:
    return {
        "plan_scoped_only": True,
        "bundle_scoped_only": True,
        "platform_read": platform_read,
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


def _fail(error: str, *, platform_read: bool = False, **detail) -> dict:
    return {
        "ok": False,
        "error": error,
        **detail,
        "execution_boundary": _boundary(platform_read=platform_read),
    }


def _request_allowed(*, workflow_key: str, expected_plan_id: int,
                     expected_status: str, bundle_id: str,
                     expected_source_sha256: str,
                     expected_policy_sha256: str,
                     expected_manifest_sha256: str,
                     expected_item_scope_sha256: str,
                     recovery_id: str,
                     expected_web_agent_commit: str) -> bool:
    return all((
        workflow_key == WORKFLOW_KEY,
        expected_plan_id == PLAN_ID,
        expected_status == EXPECTED_STATUS,
        bundle_id == BUNDLE_ID,
        expected_source_sha256 == SOURCE_SHA256,
        expected_policy_sha256 == POLICY_SHA256,
        expected_manifest_sha256 == MANIFEST_SHA256,
        expected_item_scope_sha256 == ITEM_SCOPE_SHA256,
        recovery_id == RECOVERY_ID,
        expected_web_agent_commit == EXPECTED_WEB_AGENT_COMMIT,
    ))


def _row_items(rows: list[dict]) -> set[str]:
    return {str(row.get("taobao_item_id") or "") for row in rows}


def _manifest_sha(identity: dict, policy_sha: str, signup_rows: list[dict],
                  discount_rows: list[dict]) -> str:
    return _canonical_sha256({
        "identity": identity,
        "policy_sha256": policy_sha,
        "signup_rows": signup_rows,
        "discount_rows": discount_rows,
    })


def _bundle_error(bundle: CampaignPreparationBundle | None,
                  plan: CampaignPlan, *, require_unconsumed: bool = True) -> dict | None:
    if bundle is None:
        return {"error": "final_closeout_bundle_not_found"}
    now = datetime.now(timezone.utc)
    expires_at = bundle.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    summary = bundle.summary if isinstance(bundle.summary, dict) else {}
    decisions = {
        str(row.get("taobao_item_id") or ""): str(row.get("state") or "")
        for row in (bundle.item_decisions or []) if isinstance(row, dict)
    }
    signup_rows = list(bundle.signup_rows or [])
    discount_rows = list(bundle.discount_rows or [])
    expected_decisions = {
        TARGET_ITEM_ID: "ready",
        **{item_id: "deferred_whole_item" for item_id in DEFERRED_ITEM_IDS},
    }
    checks = [
        (bundle.plan_id == PLAN_ID, "bundle_plan_mismatch"),
        (bundle.workflow_key == WORKFLOW_KEY, "bundle_workflow_mismatch"),
        (bundle.state == "ready_for_final_submission", "bundle_not_ready"),
        (expires_at > now, "bundle_expired"),
        (bundle.source_sha256 == SOURCE_SHA256, "bundle_source_sha_mismatch"),
        (bundle.policy_sha256 == POLICY_SHA256, "bundle_policy_sha_mismatch"),
        (bundle.manifest_sha256 == MANIFEST_SHA256,
         "bundle_manifest_sha_mismatch"),
        (summary.get("compiler_schema_version") == "2026-09-03.3",
         "bundle_schema_mismatch"),
        (summary.get("exact_item_scope") == sorted(
            {TARGET_ITEM_ID, *DEFERRED_ITEM_IDS}), "bundle_item_scope_mismatch"),
        (summary.get("exact_item_scope_sha256") == ITEM_SCOPE_SHA256,
         "bundle_item_scope_sha_mismatch"),
        (summary.get("global_blockers") == [], "bundle_global_blocked"),
        (decisions == expected_decisions, "bundle_decision_scope_mismatch"),
        (len(signup_rows) == EXPECTED_SIGNUP_ROWS,
         "bundle_signup_row_count_mismatch"),
        (len(discount_rows) == EXPECTED_DISCOUNT_ROWS,
         "bundle_discount_row_count_mismatch"),
        (_row_items(signup_rows) == {TARGET_ITEM_ID},
         "bundle_signup_item_mismatch"),
        (_row_items(discount_rows) == {TARGET_ITEM_ID},
         "bundle_discount_item_mismatch"),
        (_manifest_sha(bundle.identity, bundle.policy_sha256, signup_rows,
                       discount_rows) == MANIFEST_SHA256,
         "bundle_manifest_content_mismatch"),
        (plan.status == EXPECTED_STATUS, "plan_status_cas_mismatch"),
    ]
    if require_unconsumed:
        checks.append((not bundle.consumed_attempt_id, "bundle_already_consumed"))
    for ok, error in checks:
        if not ok:
            return {
                "error": error,
                "bundle_id": getattr(bundle, "id", None),
                "plan_status": plan.status,
                "consumed_attempt_id": getattr(bundle, "consumed_attempt_id", None),
            }
    return None


def _current_manifest(db: Session, plan: CampaignPlan) -> tuple[dict, list[dict], list[dict]]:
    policy = campaign_policy_service.require_policy()
    signup_rows, _ = campaign_service.build_signup_rows(db, plan)
    discount_rows, _ = campaign_service.build_discount_rows(db, plan)
    signup_rows = [row for row in signup_rows
                   if str(row.get("taobao_item_id") or "") == TARGET_ITEM_ID]
    discount_rows = [row for row in discount_rows
                     if str(row.get("taobao_item_id") or "") == TARGET_ITEM_ID]
    identity = campaign_service.campaign_identity(plan)
    return policy, signup_rows, discount_rows


def _current_manifest_error(db: Session, plan: CampaignPlan,
                            bundle: CampaignPreparationBundle) -> dict | None:
    policy, signup_rows, discount_rows = _current_manifest(db, plan)
    if (str(policy.get("_sha256") or "") != POLICY_SHA256
            or len(signup_rows) != EXPECTED_SIGNUP_ROWS
            or len(discount_rows) != EXPECTED_DISCOUNT_ROWS
            or _canonical_sha256(signup_rows) != _canonical_sha256(
                list(bundle.signup_rows or []))
            or _canonical_sha256(discount_rows) != _canonical_sha256(
                list(bundle.discount_rows or []))
            or _manifest_sha(
                campaign_service.campaign_identity(plan), POLICY_SHA256,
                signup_rows, discount_rows) != MANIFEST_SHA256):
        return {
            "error": "final_closeout_current_manifest_drift",
            "signup_rows": len(signup_rows),
            "discount_rows": len(discount_rows),
        }
    checks = campaign_service.preflight(
        db, plan, exact_item_scope={TARGET_ITEM_ID})
    blocking = [row for row in checks if row.get("level") == "error"]
    if blocking:
        return {"error": "final_closeout_preflight_blocked", "blocking": blocking}
    return None


def _replay_or_block(db: Session, plan: CampaignPlan,
                     bundle: CampaignPreparationBundle) -> dict:
    attempt = (db.get(CampaignExecutionAttempt, bundle.consumed_attempt_id)
               if bundle.consumed_attempt_id else None)
    if attempt is not None and attempt.state == "completed" \
            and plan.status == "reconciled":
        return {
            "ok": True,
            "idempotent_replay": True,
            "workflow_key": WORKFLOW_KEY,
            "plan_id": PLAN_ID,
            "plan_status": plan.status,
            "bundle_id": BUNDLE_ID,
            "attempt_id": attempt.id,
            "result": attempt.result_summary or {},
            "execution_boundary": _boundary(platform_read=True,
                                               platform_write=True),
        }
    return _fail(
        "final_closeout_already_claimed_no_retry",
        bundle_id=BUNDLE_ID,
        consumed_attempt_id=bundle.consumed_attempt_id,
        attempt_state=getattr(attempt, "state", None),
        plan_status=plan.status,
    )


def execute_plan7_final_closeout(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, bundle_id: str,
        expected_source_sha256: str, expected_policy_sha256: str,
        expected_manifest_sha256: str,
        expected_item_scope_sha256: str,
        recovery_id: str,
        expected_web_agent_commit: str) -> dict:
    """Execute the one remaining safe item and close plan 7 structurally."""
    if not _request_allowed(
            workflow_key=workflow_key, expected_plan_id=expected_plan_id,
            expected_status=expected_status, bundle_id=bundle_id,
            expected_source_sha256=expected_source_sha256,
            expected_policy_sha256=expected_policy_sha256,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_item_scope_sha256=expected_item_scope_sha256,
            recovery_id=recovery_id,
            expected_web_agent_commit=expected_web_agent_commit):
        return _fail("final_closeout_request_not_allowed")

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.id == BUNDLE_ID,
    ).with_for_update()).scalar_one_or_none()
    if bundle is not None and bundle.consumed_attempt_id:
        return _replay_or_block(db, plan, bundle)
    error = _bundle_error(bundle, plan)
    if error:
        return _fail(error.pop("error"), **error)
    if (plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or str(plan.qn_campaign_title or "").strip() != "超级立减"):
        return _fail("final_closeout_plan_identity_mismatch")
    current_error = _current_manifest_error(db, plan, bundle)
    if current_error:
        return _fail(current_error.pop("error"), **current_error)

    # This official product export is the final read-only identity guard.  No
    # durable write claim or bundle consumption exists if it fails.
    official_identity = campaign_service._refresh_official_product_sku_identity(
        db, list(bundle.signup_rows or []), plan=plan)
    if (not official_identity.get("ok")
            or official_identity.get("checked_items") != 1
            or official_identity.get("checked_skus") != EXPECTED_SIGNUP_ROWS):
        return _fail(
            "final_closeout_official_sku_identity_failed",
            platform_read=True, official_product_sku_identity=official_identity)

    # Re-lock and revalidate after the slow platform read.  This is the CAS
    # point immediately before the one-shot attempt becomes consumable.
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID).with_for_update()).scalar_one()
    bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.id == BUNDLE_ID
    ).with_for_update()).scalar_one()
    error = _bundle_error(bundle, plan)
    if error:
        return _fail(error.pop("error"), platform_read=True, **error)
    current_error = _current_manifest_error(db, plan, bundle)
    if current_error:
        return _fail(current_error.pop("error"), platform_read=True,
                     **current_error)

    policy, signup_rows, _discount_rows = _current_manifest(db, plan)
    execution_scope_sha = campaign_execution_service.scope_sha256(
        identity=campaign_service.campaign_identity(plan), rows=signup_rows,
        policy_sha256=str(policy.get("_sha256") or ""))
    attempt, created = campaign_execution_service.ensure_attempt(
        db, plan=plan, scope_sha256_value=execution_scope_sha,
        result_summary={
            "prepared_bundle_id": BUNDLE_ID,
            "prepared_bundle_source_sha256": SOURCE_SHA256,
            "prepared_bundle_policy_sha256": POLICY_SHA256,
            "prepared_bundle_manifest_sha256": MANIFEST_SHA256,
            "prepared_bundle_item_scope_sha256": ITEM_SCOPE_SHA256,
            "target_item_id": TARGET_ITEM_ID,
            "signup_rows": EXPECTED_SIGNUP_ROWS,
            "discount_rows_verified": EXPECTED_DISCOUNT_ROWS,
            "recovery_id": RECOVERY_ID,
            "expected_web_agent_commit": EXPECTED_WEB_AGENT_COMMIT,
            "deferred_item_ids": sorted(DEFERRED_ITEM_IDS),
            "preserved_active_item_ids": sorted(PRESERVED_ACTIVE_ITEM_IDS),
            "official_product_sku_identity": official_identity,
        })
    if not created:
        return _fail(
            "final_closeout_existing_signup_attempt_blocks_execution",
            platform_read=True, attempt_id=attempt.id,
            attempt_state=attempt.state, write_claimed=attempt.write_claimed)

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID).with_for_update()).scalar_one()
    bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.id == BUNDLE_ID
    ).with_for_update()).scalar_one()
    if plan.status != EXPECTED_STATUS or bundle.consumed_attempt_id:
        return _fail(
            "final_closeout_cas_changed_before_claim", platform_read=True,
            plan_status=plan.status,
            consumed_attempt_id=bundle.consumed_attempt_id,
            prepared_attempt_id=attempt.id)
    bundle.consumed_at = datetime.now(timezone.utc)
    bundle.consumed_attempt_id = attempt.id
    plan.status = "resume_executing"
    db.commit()

    try:
        result = campaign_service.push_signup(
            db, plan,
            execution_source=EXECUTION_SOURCE,
            reuse_fresh_plan_evidence=True,
            exact_item_scope={TARGET_ITEM_ID},
            allow_terminal_no_sales_fallback=False,
            prepared_official_product_identity=official_identity,
            prepared_bundle_context={
                "bundle_id": BUNDLE_ID,
                "source_sha256": SOURCE_SHA256,
                "policy_sha256": POLICY_SHA256,
                "manifest_sha256": MANIFEST_SHA256,
                "item_scope_sha256": ITEM_SCOPE_SHA256,
            },
        )
    except Exception as exc:  # noqa: BLE001 - claimed state must fail closed
        db.rollback()
        plan = db.get(CampaignPlan, PLAN_ID)
        if plan is not None:
            plan.status = "alarmed"
            db.commit()
        attempt = db.get(CampaignExecutionAttempt, attempt.id)
        if attempt is not None and attempt.write_claimed \
                and attempt.state == "write_claimed":
            campaign_execution_service.record_platform_terminal(
                db, attempt, state="unknown_no_retry",
                platform_write_observed=None,
                step="plan7_final_closeout_exception",
                error_code=type(exc).__name__,
                result_summary={"bundle_id": BUNDLE_ID})
        return {
            "ok": False,
            "error": "final_closeout_unknown_outcome_no_retry",
            "attempt_id": attempt.id if attempt else None,
            "plan_status": getattr(plan, "status", None),
            "execution_boundary": _boundary(
                platform_read=True, platform_write=None),
        }

    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "final_closeout_failed_no_retry",
            "workflow_key": WORKFLOW_KEY,
            "plan_id": PLAN_ID,
            "plan_status": getattr(plan, "status", None),
            "bundle_id": BUNDLE_ID,
            "attempt_id": attempt.id,
            "result": result,
            "execution_boundary": _boundary(
                platform_read=True,
                platform_write=(True if result.get("submitted") else None)),
        }

    plan = db.get(CampaignPlan, PLAN_ID)
    plan.status = "reconciled"
    marker = (
        f"final_closeout_bundle={BUNDLE_ID}; "
        f"final_closeout_ready_item={TARGET_ITEM_ID}; "
        f"final_closeout_deferred_items={','.join(sorted(DEFERRED_ITEM_IDS))}; "
        f"final_closeout_scope_sha256={ITEM_SCOPE_SHA256}"
    )
    if marker not in str(plan.remark or ""):
        plan.remark = f"{plan.remark or ''}; {marker}".strip("; ")
    db.commit()
    attempt = db.get(CampaignExecutionAttempt, attempt.id)
    summary = dict(attempt.result_summary or {})
    summary.update({
        "final_closeout": True,
        "bundle_id": BUNDLE_ID,
        "recovery_id": RECOVERY_ID,
        "expected_web_agent_commit": EXPECTED_WEB_AGENT_COMMIT,
        "target_item_id": TARGET_ITEM_ID,
        "deferred_item_ids": sorted(DEFERRED_ITEM_IDS),
        "preserved_active_item_ids": sorted(PRESERVED_ACTIVE_ITEM_IDS),
        "plan_status": plan.status,
    })
    attempt.result_summary = summary
    db.commit()
    return {
        "ok": True,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "plan_status": plan.status,
        "bundle_id": BUNDLE_ID,
        "recovery_id": RECOVERY_ID,
        "expected_web_agent_commit": EXPECTED_WEB_AGENT_COMMIT,
        "attempt_id": attempt.id,
        "scope_sha256": execution_scope_sha,
        "submitted_item_ids": [TARGET_ITEM_ID],
        "deferred_item_ids": sorted(DEFERRED_ITEM_IDS),
        "preserved_active_item_ids": sorted(PRESERVED_ACTIVE_ITEM_IDS),
        "exempt_item_ids": sorted(EXEMPT_ITEM_IDS),
        "result": result,
        "execution_boundary": _boundary(
            platform_read=True, platform_write=True),
    }

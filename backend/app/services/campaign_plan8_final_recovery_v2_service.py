"""Final full-SKU recovery for the 2026-09 Super88 plan 8.

The retired recovery remains immutable evidence.  This V2 flow refreshes live
candidate evidence, supplements only the eight newly mapped ordinary SKUs in
their existing single-item-discount activities, then enrolls the exact six
pending products with all 78 live SKUs, including 18 custom SKUs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_execution_service,
    campaign_policy_service,
    campaign_service,
    web_agent_service,
)


WORKFLOW_KEY = "campaign:super88:49462:49469"
PLAN_ID = 8
EXPECTED_STATUS = "alarmed"
RECOVERY_VERSION = 2
OPERATION = "plan8_final_recovery_v2"
V2_RETIRED = True
EXPECTED_POLICY_SHA256 = (
    "0209e67546c2e20be904a54d16402a32de02d91129bc1cceeda34b6e6fa4483f"
)
EXPECTED_FULL_SCOPE_SHA256 = (
    "e20bf804400d839720e458217968c2e9122bad07d4a9fb21dd78cafe7ce172ef"
)
EXPECTED_TARGET_SCOPE_SHA256 = (
    "b239dc515b0f2442257e90fe30a1cda95e29f6ffd2ea123d6c53f6fd6a4feb1d"
)
OLD_CANDIDATE_SHA256 = (
    "bddba1f579359389d85928c0ccff75b7e9595ac767504121de16b3c661560070"
)
EXPECTED_UNAVAILABLE_ITEM_IDS = {"793202812082"}
EXPECTED_ALREADY_PUBLISHED_ITEM_IDS = {
    "1001358847694", "805268708396", "863525290377",
}
EXPECTED_TARGET_ITEM_IDS = {
    "1036279566778",
    "1036312802226",
    "1074244132390",
    "837902729785",
    "841201084787",
    "917179577721",
}
EXPECTED_OFFICIAL_RECORD_ITEM_IDS = (
    EXPECTED_ALREADY_PUBLISHED_ITEM_IDS | EXPECTED_TARGET_ITEM_IDS
)
EXPECTED_FULL_ROW_COUNT = 85
EXPECTED_TARGET_ROW_COUNT = 78
EXPECTED_TARGET_CUSTOM_ROW_COUNT = 18
SUPPLEMENT_SKU_IDS = {
    "6234601898881", "6234601898883", "6234601898885", "6234601898887",
    "6287431318354", "6287431318356", "6287431318358", "6287431318360",
}
SUPPLEMENT_PAIRS = {
    ("1036279566778", sku_id) for sku_id in {
        "6234601898881", "6234601898883", "6234601898885", "6234601898887",
    }
} | {
    ("1074244132390", sku_id) for sku_id in {
        "6287431318354", "6287431318356", "6287431318358", "6287431318360",
    }
}
PREREQUISITE_ATTEMPTS = {
    "14ddfc8e428148b66f61c7aa": ("plan8_discount_and_signup", "failed_no_retry", True),
    "a3d7dfd9d65d7a5e62ad4afd": ("signup", "failed_no_retry", True),
    "26b67a144f9448d65ef56c66": ("plan8_signup_recovery", "failed_no_retry", True),
    "05a12142148dd04d25a88d48": ("plan8_sku_mapping_repair", "completed", True),
}


def _boundary(*, platform_write: bool = False) -> dict:
    return {
        "plan_scoped_only": True,
        "platform_read": True,
        "platform_write": platform_write,
        "erp_daily_price_change": False,
        "sku_rotation": False,
        "withdraw_pause_remove": False,
        "warehouse_item_write": False,
        "old_53_discount_rows_replayed": False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, **detail) -> dict:
    return {"ok": False, "error": error, **detail,
            "execution_boundary": _boundary()}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()


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


def _rows_scope(rows: list[dict]) -> tuple[set[str], set[str], int]:
    items = {str(row.get("taobao_item_id") or "") for row in rows}
    skus = {str(row.get("taobao_sku_id") or "") for row in rows}
    custom = sum(1 for row in rows if row.get("is_placeholder") is True)
    return items, skus, custom


def _scope_sha(identity: dict, rows: list[dict], policy_sha: str) -> str:
    return campaign_execution_service.scope_sha256(
        identity=identity, rows=rows, policy_sha256=policy_sha)


def _validate_prerequisites(db: Session) -> tuple[bool, list[dict]]:
    detail = []
    ok = True
    for attempt_id, expected in PREREQUISITE_ATTEMPTS.items():
        row = db.get(CampaignExecutionAttempt, attempt_id)
        actual = {
            "attempt_id": attempt_id,
            "operation": getattr(row, "operation", None),
            "state": getattr(row, "state", None),
            "write_claimed": getattr(row, "write_claimed", None),
        }
        detail.append(actual)
        if row is None or (
            actual["operation"], actual["state"], actual["write_claimed"]
        ) != expected:
            ok = False
    return ok, detail


def _discount_scope(rows: list[dict]) -> list[dict[str, str]]:
    scope = []
    for row in rows:
        item_id = str(row.get("taobao_item_id") or "")
        sku_id = str(row.get("taobao_sku_id") or "")
        if (item_id, sku_id) not in SUPPLEMENT_PAIRS:
            continue
        amount = Decimal(str(row.get("deduct"))).quantize(Decimal("0.01"))
        scope.append({
            "item_id": item_id,
            "sku_id": sku_id,
            "expected_deduct": f"{amount:.2f}",
        })
    return sorted(scope, key=lambda row: (row["item_id"], row["sku_id"]))


def _discount_scope_sha(scope: list[dict]) -> str:
    payload = [[row["item_id"], row["sku_id"], row["expected_deduct"]]
               for row in scope]
    return _hash(payload)


def validate_prepared_current_activity(current: dict) -> tuple[bool, dict]:
    rows = current.get("rows") if isinstance(current, dict) else None
    export = current.get("export_evidence") if isinstance(current, dict) else None
    candidate = current.get("candidate_evidence") if isinstance(current, dict) else None
    unavailable = current.get("candidate_unavailable") if isinstance(current, dict) else None
    marketing = export.get("marketing_records") if isinstance(export, dict) else None
    identity = export.get("identity") if isinstance(export, dict) else None
    active_items = {str(row.get("item_id") or "") for row in (rows or [])
                    if str(row.get("item_id") or "")}
    record_items = {str(row.get("item_id") or "") for row in (marketing or [])
                    if str(row.get("item_id") or "")}
    selected = {str(row.get("item_id") or "") for row in (marketing or [])
                if row.get("selected") is True
                and row.get("proves_enrollment") is True}
    candidate_sha = str((candidate or {}).get("sha256") or "")
    requested = (candidate or {}).get("requested_sku_count")
    observed = (candidate or {}).get("observed_sku_count")
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
        and isinstance(export, dict)
        and re.fullmatch(r"[0-9a-f]{64}", str(export.get("sha256") or ""))
        and isinstance(identity, dict)
        and all(str(identity.get(key) or "") == value
                for key, value in expected_identity.items())
        and active_items == EXPECTED_ALREADY_PUBLISHED_ITEM_IDS
        and isinstance(marketing, list)
        and record_items == EXPECTED_OFFICIAL_RECORD_ITEM_IDS
        and selected == EXPECTED_ALREADY_PUBLISHED_ITEM_IDS
        and not (selected & EXPECTED_TARGET_ITEM_IDS)
        and isinstance(candidate, dict)
        and re.fullmatch(r"[0-9a-f]{64}", candidate_sha)
        and candidate_sha != OLD_CANDIDATE_SHA256
        and isinstance(requested, int) and requested > 0
        and requested == observed
        and not (candidate.get("missing_sku_ids") or [])
        and isinstance(unavailable, dict)
        and unavailable.get("complete") is True
        and set(unavailable.get("items") or []) == EXPECTED_UNAVAILABLE_ITEM_IDS
        and not (unavailable.get("partial_missing_items") or [])
        and unavailable.get("sha256") == candidate_sha
    )
    detail = {
        "export_sha256": (export or {}).get("sha256"),
        "active_item_ids": sorted(active_items),
        "marketing_record_item_ids": sorted(record_items),
        "selected_enrolled_item_ids": sorted(selected),
        "candidate_sha256": candidate_sha or None,
        "candidate_requested_skus": requested,
        "candidate_observed_skus": observed,
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
        "plan8_final_v2_already_claimed_no_retry",
        attempt_id=attempt.id,
        attempt_state=attempt.state,
        platform_write_observed=attempt.platform_write_observed,
        plan_status=plan.status,
    )


def recover_plan8_final_v2(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, recovery_version: int) -> dict:
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_status != EXPECTED_STATUS
            or recovery_version != RECOVERY_VERSION):
        return _fail("plan8_final_v2_request_not_allowed")
    # V2's candidate-picker completeness premise is invalid for products that
    # already have official draft records.  Keep the historical code and its
    # failed read-only evidence, but permanently prevent another invocation.
    if V2_RETIRED:
        return _fail("plan8_final_v2_retired_use_v3")

    # Historical implementation remains covered as immutable audit context.
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    identity_ok, identity = _identity_allowed(plan)
    if not identity_ok:
        return _fail("plan8_final_v2_identity_not_allowed", identity=identity)
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
    )).scalar_one_or_none()
    if existing is not None:
        return _replay(existing, plan)
    if plan.status != EXPECTED_STATUS:
        return _fail("plan8_final_v2_status_cas_mismatch",
                     actual_status=plan.status)
    prerequisites_ok, prerequisite_detail = _validate_prerequisites(db)
    if not prerequisites_ok:
        return _fail("plan8_final_v2_prerequisite_attempt_mismatch",
                     attempts=prerequisite_detail)
    policy = campaign_policy_service.require_policy()
    policy_sha = str(policy.get("_sha256") or "")
    if policy_sha != EXPECTED_POLICY_SHA256:
        return _fail("plan8_final_v2_policy_changed",
                     expected_policy_sha256=EXPECTED_POLICY_SHA256,
                     actual_policy_sha256=policy_sha)

    full_rows, full_stats = campaign_service.build_signup_rows(db, plan)
    target_rows = [row for row in full_rows
                   if str(row.get("taobao_item_id") or "")
                   in EXPECTED_TARGET_ITEM_IDS]
    full_items, full_skus, _ = _rows_scope(full_rows)
    target_items, target_skus, target_custom = _rows_scope(target_rows)
    full_scope = _scope_sha(identity, full_rows, policy_sha)
    target_scope = _scope_sha(identity, target_rows, policy_sha)
    if not (
        len(full_rows) == EXPECTED_FULL_ROW_COUNT
        and len(full_skus) == EXPECTED_FULL_ROW_COUNT
        and full_items == EXPECTED_TARGET_ITEM_IDS | {"805268708396"}
        and full_scope == EXPECTED_FULL_SCOPE_SHA256
        and len(target_rows) == EXPECTED_TARGET_ROW_COUNT
        and len(target_skus) == EXPECTED_TARGET_ROW_COUNT
        and target_items == EXPECTED_TARGET_ITEM_IDS
        and target_custom == EXPECTED_TARGET_CUSTOM_ROW_COUNT
        and target_scope == EXPECTED_TARGET_SCOPE_SHA256
    ):
        return _fail(
            "plan8_final_v2_signup_scope_drift",
            full_rows=len(full_rows), target_rows=len(target_rows),
            target_custom_rows=target_custom,
            full_scope_sha256=full_scope,
            target_scope_sha256=target_scope,
            stats=full_stats,
        )

    # Mandatory current-state refresh before the first platform write.
    current = campaign_service.refresh_floor_evidence_from_current_activity(db, plan)
    if not current.get("ok"):
        return _fail("plan8_final_v2_readonly_refresh_failed",
                     step=current.get("step"), detail=current.get("detail"),
                     job_id=current.get("job_id"),
                     platform_error=current.get("error"),
                     need_scan=bool(current.get("need_scan")))
    current_ok, current_detail = validate_prepared_current_activity(current)
    if not current_ok:
        return _fail("plan8_final_v2_current_activity_mismatch",
                     current_activity=current_detail)

    db.expire_all()
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None or plan.status != EXPECTED_STATUS:
        return _fail("plan8_final_v2_state_changed_after_refresh",
                     actual_status=getattr(plan, "status", None))
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
    )).scalar_one_or_none()
    if existing is not None:
        return _replay(existing, plan)
    refreshed_rows, refreshed_stats = campaign_service.build_signup_rows(db, plan)
    refreshed_target = [row for row in refreshed_rows
                        if str(row.get("taobao_item_id") or "")
                        in EXPECTED_TARGET_ITEM_IDS]
    _, refreshed_skus, refreshed_custom = _rows_scope(refreshed_target)
    if (len(refreshed_target) != EXPECTED_TARGET_ROW_COUNT
            or len(refreshed_skus) != EXPECTED_TARGET_ROW_COUNT
            or refreshed_custom != EXPECTED_TARGET_CUSTOM_ROW_COUNT
            or _scope_sha(identity, refreshed_target, policy_sha)
            != EXPECTED_TARGET_SCOPE_SHA256):
        return _fail("plan8_final_v2_scope_changed_after_refresh",
                     row_count=len(refreshed_target),
                     custom_row_count=refreshed_custom,
                     stats=refreshed_stats)
    unavailable = campaign_service.candidate_unavailable_items_for_plan(db, plan)
    candidate_sha = current_detail["candidate_sha256"]
    if (set(unavailable) != EXPECTED_UNAVAILABLE_ITEM_IDS
            or {str(row.get("evidence_sha256") or "")
                for row in unavailable.values()} != {candidate_sha}):
        return _fail("plan8_final_v2_candidate_state_mismatch",
                     unavailable_item_ids=sorted(unavailable))

    checks = campaign_service.preflight(
        db, plan, exact_item_scope=EXPECTED_TARGET_ITEM_IDS)
    by_rule = {str(row.get("rule") or ""): row for row in checks}
    blocking = [row for row in checks if row.get("level") == "error"]
    if (blocking or by_rule.get("R16", {}).get("level") != "pass"
            or by_rule.get("R17", {}).get("level") != "pass"):
        return _fail("plan8_final_v2_preflight_blocked", blocking=blocking,
                     gate_results={"R16": by_rule.get("R16"),
                                   "R17": by_rule.get("R17")})

    official_identity = campaign_service._refresh_official_product_sku_identity(
        db, refreshed_target, plan=plan)
    if (not official_identity.get("ok")
            or official_identity.get("checked_items") != 6
            or official_identity.get("checked_skus") != EXPECTED_TARGET_ROW_COUNT
            or official_identity.get("official_skus") != EXPECTED_TARGET_ROW_COUNT
            or official_identity.get("excluded_custom_skus") != 0):
        return _fail("plan8_final_v2_official_sku_identity_blocked",
                     official_product_sku_identity=official_identity)

    discount_rows, discount_stats = campaign_service.build_discount_rows(db, plan)
    supplement_rows = [row for row in discount_rows
                       if str(row.get("taobao_sku_id") or "")
                       in SUPPLEMENT_SKU_IDS]
    supplement_pairs = {(str(row.get("taobao_item_id") or ""),
                         str(row.get("taobao_sku_id") or ""))
                        for row in supplement_rows}
    if len(supplement_rows) != 8 or supplement_pairs != SUPPLEMENT_PAIRS:
        return _fail("plan8_final_v2_discount_scope_drift",
                     row_count=len(supplement_rows),
                     pairs=sorted(supplement_pairs), stats=discount_stats)
    inspect_scope = _discount_scope(supplement_rows)
    inspect_sha = _discount_scope_sha(inspect_scope)
    inspection = web_agent_service.inspect_plan8_final_discount_supplement(
        db,
        payload={
            "workflow_key": WORKFLOW_KEY,
            "plan_id": PLAN_ID,
            "start_at": "2026-09-06 20:00:00",
            "end_at": "2026-09-13 23:59:59",
            "scope": inspect_scope,
            "scope_sha256": inspect_sha,
        },
    )
    inspected_pairs = {(str(row.get("item_id") or ""),
                        str(row.get("sku_id") or ""))
                       for row in inspection.get("rows") or []}
    if (not inspection.get("ok") or inspected_pairs != SUPPLEMENT_PAIRS
            or inspection.get("scope_sha256") != inspect_sha
            or inspection.get("wrong_skus")):
        return _fail("plan8_final_v2_discount_inspection_blocked",
                     inspection=inspection,
                     need_scan=bool(inspection.get("need_scan")))
    activity_by_item = {
        str(row.get("item_id")): str(row.get("activity_id"))
        for row in inspection.get("items") or []
    }
    if set(activity_by_item) != {pair[0] for pair in SUPPLEMENT_PAIRS}:
        return _fail("plan8_final_v2_discount_activity_binding_mismatch",
                     activity_by_item=activity_by_item)
    correct_skus = set(inspection.get("correct_skus") or [])
    rows_to_write = [row for row in supplement_rows
                     if str(row.get("taobao_sku_id") or "") not in correct_skus]
    outer_scope = _hash({
        "workflow_key": WORKFLOW_KEY,
        "version": RECOVERY_VERSION,
        "policy_sha256": policy_sha,
        "candidate_sha256": candidate_sha,
        "signup_scope_sha256": EXPECTED_TARGET_SCOPE_SHA256,
        "discount_scope_sha256": inspect_sha,
        "activity_by_item": activity_by_item,
        "rows_to_write": sorted(str(row.get("taobao_sku_id") or "")
                                for row in rows_to_write),
    })
    attempt = CampaignExecutionAttempt(
        id=secrets.token_hex(12), plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=OPERATION, scope_sha256=outer_scope, state="prepared",
        write_claimed=False, automatic_retry_allowed=False,
        result_summary={
            "policy_sha256": policy_sha,
            "candidate_sha256": candidate_sha,
            "signup_scope_sha256": EXPECTED_TARGET_SCOPE_SHA256,
            "discount_scope_sha256": inspect_sha,
            "discount_rows_to_write": len(rows_to_write),
            "discount_rows_already_correct": len(correct_skus),
            "activity_by_item": activity_by_item,
            "pre_submit_current_activity": current_detail,
            "official_product_sku_identity": official_identity,
        },
    )
    db.add(attempt)
    db.commit()
    campaign_execution_service.claim_platform_write(
        db, attempt.id,
        request_id=f"plan8-final-v2-{secrets.token_hex(8)}")

    discount_results = []
    try:
        for item_id in sorted(activity_by_item):
            item_rows = [row for row in rows_to_write
                         if str(row.get("taobao_item_id") or "") == item_id]
            if not item_rows:
                continue
            result = campaign_service._upload_and_wait(
                db, "single_item_discount", "commit",
                campaign_service._build_discount_xlsx(item_rows),
                "2026-09-06 20:00:00", "2026-09-13 23:59:59",
                plan=plan, expected_rows=len(item_rows),
                discount_activity_id=activity_by_item[item_id])
            discount_results.append({"item_id": item_id,
                                     "activity_id": activity_by_item[item_id],
                                     **result})
            if not result.get("ok"):
                campaign_execution_service.record_platform_terminal(
                    db, attempt, state="failed_no_retry",
                    platform_write_observed=bool(result.get("submitted")),
                    step="plan8_final_v2_discount_supplement",
                    error_code=str(result.get("error") or result.get("message")
                                   or "discount_supplement_failed"),
                    job_id=str(result.get("job") or "") or None,
                    result_summary={"discount_results": discount_results})
                return _fail("plan8_final_v2_discount_failed_no_retry",
                             attempt_id=attempt.id,
                             discount_results=discount_results)

        sku_activity_ids = {
            **campaign_service._plan_single_discount_sku_activity_ids(plan),
            **{sku_id: activity_by_item[item_id]
               for item_id, sku_id in SUPPLEMENT_PAIRS},
        }
        campaign_service._set_plan_single_discount_sku_activity_ids(
            plan, sku_activity_ids)
        plan.status = "resume_executing"
        db.commit()
        signup = campaign_service.push_signup(
            db, plan,
            execution_source="campaign_super88_plan8_final_recovery_v2",
            reuse_fresh_plan_evidence=True,
            exact_item_scope=EXPECTED_TARGET_ITEM_IDS,
            allow_terminal_no_sales_fallback=False,
            prepared_current_activity=current,
            prepared_official_product_identity=official_identity,
        )
    except Exception as exc:
        db.rollback()
        plan = db.get(CampaignPlan, PLAN_ID)
        if plan is not None:
            plan.status = "alarmed"
            db.commit()
        attempt = db.get(CampaignExecutionAttempt, attempt.id)
        campaign_execution_service.record_platform_terminal(
            db, attempt, state="unknown_no_retry",
            platform_write_observed=None,
            step="plan8_final_v2_exception",
            error_code=type(exc).__name__,
            result_summary={"discount_results": discount_results})
        return _fail("plan8_final_v2_unknown_outcome_no_retry",
                     attempt_id=attempt.id, error_type=type(exc).__name__)

    completed = bool(signup.get("ok"))
    submitted = bool(signup.get("submitted")) or bool(discount_results)
    plan = db.get(CampaignPlan, PLAN_ID)
    if not completed and plan is not None:
        plan.status = "alarmed"
        db.commit()
    summary = {
        "candidate_sha256": candidate_sha,
        "signup_scope_sha256": EXPECTED_TARGET_SCOPE_SHA256,
        "discount_scope_sha256": inspect_sha,
        "discount_rows_written": len(rows_to_write),
        "discount_rows_already_correct": len(correct_skus),
        "discount_results": discount_results,
        "signup": signup,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    campaign_execution_service.record_platform_terminal(
        db, attempt,
        state="completed" if completed else "failed_no_retry",
        platform_write_observed=submitted,
        step="completed" if completed else str(
            signup.get("step") or "plan8_final_v2_signup_failed"),
        error_code=None if completed else str(
            signup.get("error") or "plan8_final_v2_signup_failed"),
        job_id=str(signup.get("job") or signup.get("job_id") or "") or None,
        result_summary=summary,
    )
    response = {
        "ok": completed,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "plan_status": getattr(plan, "status", None),
        "attempt_id": attempt.id,
        "signup_attempt_id": (signup.get("stats") or {}).get(
            "execution_attempt_id"),
        "scope_sha256": outer_scope,
        "candidate_sha256": candidate_sha,
        "discount_rows_written": len(rows_to_write),
        "discount_rows_already_correct": len(correct_skus),
        "discount_results": discount_results,
        "signup": signup,
        "execution_boundary": _boundary(platform_write=submitted),
    }
    if not completed:
        response["error"] = signup.get("error") or "plan8_final_v2_signup_failed_no_retry"
    return response

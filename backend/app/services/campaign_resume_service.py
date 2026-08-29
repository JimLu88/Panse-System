"""One-shot, fail-closed recovery for the approved Super Reduce plan 7.

This is intentionally not a generic campaign retry service.  It accepts one
durable workflow/plan identity and one reviewed row fingerprint.  It reuses the
fresh plan-scoped price evidence already persisted by the formal refresh, then
allows exactly one signup upload.  A claimed attempt is never automatically
retried, including when its final outcome becomes unknown.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignPlan
from app.services import (
    campaign_policy_service,
    campaign_price_floor_service,
    campaign_service,
    settings_service,
)


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
EXPECTED_STATUS = "alarmed"
EXPECTED_ITEM_ID = "797294092429"
EXPECTED_SKU_IDS = {"6292834839399", "6292834839400"}
EXPECTED_EXEMPT_ITEM_IDS = {"805268708396"}
EXPECTED_SCOPE_SHA256 = (
    "73d73f5e78d5f7149b4425f6c7e9909e9892f037d4859498e6dea26f0163b7a4"
)
ATTEMPT_KEY = "campaign_plan7_resume_execute_v1"


def _load_json_setting(db: Session, key: str):
    raw = settings_service.get(db, key, env_fallback=False)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"status": "invalid"}
    return value if isinstance(value, dict) else {"status": "invalid"}


def _save_attempt(db: Session, payload: dict) -> None:
    settings_service.set_value(
        db,
        ATTEMPT_KEY,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        description="超级立减计划7一次性恢复执行CAS/幂等回执（不含凭据）",
    )


def _execution_receipts(db: Session) -> list[dict] | None:
    raw = settings_service.get(
        db, f"campaign_execution_receipts_{PLAN_ID}", env_fallback=False)
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    return rows


def _rounded(row: dict, key: str) -> float:
    return round(float(row.get(key)), 2)


def _scope_snapshot(db: Session, plan: CampaignPlan) -> tuple[dict, list[dict], list[dict]]:
    signup_rows, _ = campaign_service.build_signup_rows(db, plan)
    signup_rows = [
        row for row in signup_rows
        if str(row.get("taobao_item_id") or "") == EXPECTED_ITEM_ID
    ]
    discount_rows, _ = campaign_service.build_discount_rows(db, plan)
    discount_rows = [
        row for row in discount_rows
        if str(row.get("taobao_item_id") or "") == EXPECTED_ITEM_ID
        and str(row.get("taobao_sku_id") or "") in EXPECTED_SKU_IDS
    ]
    signup = sorted(({
        "item_id": str(row.get("taobao_item_id") or ""),
        "sku_id": str(row.get("taobao_sku_id") or ""),
        "price": _rounded(row, "price"),
    } for row in signup_rows), key=lambda row: row["sku_id"])
    discount = sorted(({
        "item_id": str(row.get("taobao_item_id") or ""),
        "sku_id": str(row.get("taobao_sku_id") or ""),
        "deduct": _rounded(row, "deduct"),
        "official": _rounded(row, "official"),
        "final": _rounded(row, "target_price"),
        "base": _rounded(row, "calculation_base"),
    } for row in discount_rows), key=lambda row: row["sku_id"])
    snapshot = {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "signup": signup,
        "discount": discount,
    }
    return snapshot, signup_rows, discount_rows


def _snapshot_digest(snapshot: dict) -> str:
    payload = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _execution_boundary(*, submitted: bool = False) -> dict:
    return {
        "plan_scoped_only": True,
        "pre_submit_platform_read": False,
        "reused_plan_scoped_evidence": True,
        "platform_write": bool(submitted),
        "account_action": bool(submitted),
        "price_change": False,
        "sku_rotation": False,
        "withdraw_pause_remove": False,
        "touches_plan8": False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, **detail) -> dict:
    return {
        "ok": False,
        "error": error,
        **detail,
        "execution_boundary": _execution_boundary(),
    }


def resume_super_reduce_plan7(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, expected_scope_sha256: str) -> dict:
    """CAS-claim and execute the single approved plan-7 signup attempt."""
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_status != EXPECTED_STATUS
            or expected_scope_sha256 != EXPECTED_SCOPE_SHA256):
        return _fail("resume_request_not_allowed")

    plan = db.execute(
        select(CampaignPlan).where(
            CampaignPlan.id == PLAN_ID,
            CampaignPlan.workflow_key == WORKFLOW_KEY,
        ).with_for_update()
    ).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    if (plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or str(plan.qn_campaign_title or "").strip() != "超级立减"):
        return _fail("resume_identity_not_allowed")

    receipts = _execution_receipts(db)
    if receipts is None:
        return _fail("resume_receipt_state_invalid")
    submitted_receipts = [row for row in receipts if row.get("submitted") is True]
    exact_submitted_receipts = [
        row for row in submitted_receipts
        if row.get("plan_id") == PLAN_ID
        and row.get("campaign_title") == "超级立减"
    ]
    attempt = _load_json_setting(db, ATTEMPT_KEY)
    if plan.status == "signup_pushed" and exact_submitted_receipts:
        return {
            "ok": True,
            "idempotent_replay": True,
            "workflow_key": WORKFLOW_KEY,
            "plan_id": PLAN_ID,
            "plan_status": plan.status,
            "scope_sha256": EXPECTED_SCOPE_SHA256,
            "execution_boundary": _execution_boundary(submitted=True),
        }
    if attempt:
        if (attempt.get("workflow_key") != WORKFLOW_KEY
                or attempt.get("plan_id") != PLAN_ID
                or attempt.get("scope_sha256") != EXPECTED_SCOPE_SHA256):
            return _fail("resume_attempt_state_invalid")
        if attempt.get("status") == "completed":
            return {
                "ok": True,
                "idempotent_replay": True,
                "workflow_key": WORKFLOW_KEY,
                "plan_id": PLAN_ID,
                "plan_status": plan.status,
                "scope_sha256": EXPECTED_SCOPE_SHA256,
                "attempt": attempt,
                "execution_boundary": _execution_boundary(
                    submitted=bool(attempt.get("submitted"))),
            }
        return _fail(
            "resume_attempt_already_claimed_no_retry",
            attempt_status=attempt.get("status"),
            attempt_id=attempt.get("attempt_id"),
            plan_status=plan.status,
        )
    if plan.status != expected_status:
        return _fail(
            "resume_status_cas_mismatch",
            expected_status=expected_status,
            actual_status=plan.status,
        )
    if submitted_receipts:
        return _fail("prior_submitted_receipt_blocks_resume")

    official_scope = campaign_service.official_scope_for_plan(plan)
    if (not official_scope.get("configured")
            or not official_scope.get("all_store")
            or set(official_scope.get("exempt_items") or set())
            != EXPECTED_EXEMPT_ITEM_IDS):
        return _fail(
            "resume_scope_not_allowed",
            actual_exempt_item_ids=sorted(
                official_scope.get("exempt_items") or set()),
        )
    if (campaign_service.authorized_sku_refresh_items(plan)
            or campaign_service.authorized_supplement_items(plan)):
        return _fail("resume_scope_not_allowed", reason="rotation_or_supplement_marker_present")

    snapshot, signup_rows, discount_rows = _scope_snapshot(db, plan)
    actual_digest = _snapshot_digest(snapshot)
    if (actual_digest != EXPECTED_SCOPE_SHA256
            or {row["sku_id"] for row in snapshot["signup"]} != EXPECTED_SKU_IDS
            or {row["sku_id"] for row in snapshot["discount"]} != EXPECTED_SKU_IDS
            or len(signup_rows) != 2 or len(discount_rows) != 2):
        return _fail(
            "resume_scope_drift",
            expected_scope_sha256=EXPECTED_SCOPE_SHA256,
            actual_scope_sha256=actual_digest,
            snapshot=snapshot,
        )

    checks = campaign_service.preflight(
        db, plan, exact_item_scope={EXPECTED_ITEM_ID})
    blocking = [check for check in checks if check.get("level") == "error"]
    by_rule = {check.get("rule"): check for check in checks}
    if blocking or by_rule.get("R16", {}).get("level") != "pass" \
            or by_rule.get("R17", {}).get("level") != "pass" \
            or by_rule.get("R17", {}).get("checked") != 2:
        return _fail(
            "resume_preflight_blocked",
            blocking=blocking,
            gate_results={"R16": by_rule.get("R16"), "R17": by_rule.get("R17")},
        )

    evidence = campaign_price_floor_service.evidence_map(db, plan=plan)
    max_age = campaign_policy_service.floor_evidence_max_age_hours()
    evidence_summary = []
    for sku_id in sorted(EXPECTED_SKU_IDS):
        entry = evidence.get(sku_id) if isinstance(evidence.get(sku_id), dict) else {}
        age = campaign_price_floor_service.evidence_age_hours(entry)
        if (entry.get("item_id") != EXPECTED_ITEM_ID
                or entry.get("source") != f"campaign_pre_submit_export:plan={PLAN_ID}"
                or age is None or age > max_age):
            return _fail(
                "resume_evidence_not_fresh",
                sku_id=sku_id,
                source=entry.get("source"),
                observed_at=entry.get("observed_at"),
                age_hours=round(age, 2) if age is not None else None,
                max_age_hours=max_age,
            )
        evidence_summary.append({
            "sku_id": sku_id,
            "observed_at": entry.get("observed_at"),
            "age_hours": round(age, 2),
            "source": entry.get("source"),
        })

    live_prices = campaign_service.current_activity_prices_for_plan(plan)
    if EXPECTED_SKU_IDS & set(live_prices):
        return _fail(
            "resume_live_state_changed_requires_review",
            observed_sku_ids=sorted(EXPECTED_SKU_IDS & set(live_prices)),
        )

    attempt_id = secrets.token_hex(12)
    claimed_at = datetime.now(timezone.utc).isoformat()
    claimed = {
        "status": "claimed",
        "attempt_id": attempt_id,
        "claimed_at": claimed_at,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "scope_sha256": EXPECTED_SCOPE_SHA256,
        "evidence": evidence_summary,
        "pre_submit_platform_read": False,
        "automatic_retry": False,
    }
    plan.status = "resume_executing"
    _save_attempt(db, claimed)
    db.commit()

    try:
        result = campaign_service.push_signup(
            db,
            plan,
            execution_source="campaign_super_reduce_plan7_resume",
            reuse_fresh_plan_evidence=True,
            exact_item_scope={EXPECTED_ITEM_ID},
            allow_terminal_no_sales_fallback=False,
        )
    except Exception as exc:  # noqa: BLE001 - unknown outcome must fail closed
        db.rollback()
        plan = db.get(CampaignPlan, PLAN_ID)
        if plan is not None and plan.status == "resume_executing":
            plan.status = "alarmed"
        failed = {
            **claimed,
            "status": "failed_unknown_outcome",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error_type": type(exc).__name__,
            "submitted": None,
        }
        _save_attempt(db, failed)
        db.commit()
        return _fail(
            "resume_execution_unknown_outcome_no_retry",
            attempt_id=attempt_id,
            plan_status=getattr(plan, "status", None),
        )

    submitted = bool(result.get("submitted"))
    completed = bool(result.get("ok"))
    if not completed and plan.status == "resume_executing":
        plan.status = "alarmed"
    final_attempt = {
        **claimed,
        "status": "completed" if completed else "failed",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "submitted": submitted,
        "result_step": result.get("step") or "signup_complete",
        "result_error": result.get("error"),
        "job_id": result.get("job"),
    }
    _save_attempt(db, final_attempt)
    db.commit()
    response = {
        "ok": completed,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "plan_status": plan.status,
        "attempt": final_attempt,
        "scope_sha256": EXPECTED_SCOPE_SHA256,
        "scope": snapshot,
        "result": result,
        "execution_boundary": _execution_boundary(submitted=submitted),
    }
    if not completed:
        response["error"] = result.get("error") or "resume_execution_failed_no_retry"
    return response

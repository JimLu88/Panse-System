"""One-shot four-row correction for the plan-7 single-discount partial import.

This is deliberately not a generic retry route.  It is bound to immutable
snapshot 1, one item, four physical SKUs and their reviewed ERP prices.  The
attempt is claimed before any platform interaction and can never be retried
after a failed or unknown outcome.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import secrets
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignEvidenceSnapshot, CampaignPlan
from app.services import (
    campaign_discount_audit_service,
    campaign_service,
    settings_service,
    web_agent_service,
)


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
EXPECTED_STATUS = "alarmed"
EXPECTED_SNAPSHOT_ID = 1
EXPECTED_SNAPSHOT_REQUEST_ID = "plan7-discount-audit-464fc409dce0"
EXPECTED_SNAPSHOT_ARTIFACT_SHA256 = (
    "34e5fb410ca0bed56baca4ef0681fcbfc7a8c3b81ebaa5397a51e579f32a8211"
)
EXPECTED_FULL_SCOPE_SHA256 = (
    "599fa440ba4f7e42aab4dd39423fa807ec85d4964a8df5169303ffb9c0517a18"
)
EXPECTED_MISSING_SCOPE_SHA256 = (
    "2ef18e9537abae8af10ec1a0580336e2377b1ca3a7da38d4247a9bc7bf4a9739"
)
EXPECTED_ITEM_ID = "1047741902625"
EXPECTED_ACTIVITY_ID = "143780562424"
ATTEMPT_KEY = "campaign_plan7_discount_correction_v1"

EXPECTED_ROWS = (
    {
        "item_id": EXPECTED_ITEM_ID,
        "sku_id": "6279984722445",
        "sku_code": "PFG2521002122211",
        "daily": "3390.00",
        "deduct": "612.63",
        "official": "339.00",
        "final": "2438.37",
    },
    {
        "item_id": EXPECTED_ITEM_ID,
        "sku_id": "6279984722446",
        "sku_code": "PFG2521002122212",
        "daily": "3465.00",
        "deduct": "627.08",
        "official": "347.00",
        "final": "2490.92",
    },
    {
        "item_id": EXPECTED_ITEM_ID,
        "sku_id": "6279984722447",
        "sku_code": "PFG2521002122213",
        "daily": "3577.50",
        "deduct": "644.50",
        "official": "358.00",
        "final": "2575.00",
    },
    {
        "item_id": EXPECTED_ITEM_ID,
        "sku_id": "6279984722448",
        "sku_code": "PFG2521002122214",
        "daily": "3667.50",
        "deduct": "662.44",
        "official": "367.00",
        "final": "2638.06",
    },
)


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def _money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"


def _execution_boundary(*, platform_read: bool = False,
                        submitted: bool = False) -> dict:
    return {
        "plan7_only": True,
        "snapshot_cas": True,
        "exact_item_id": EXPECTED_ITEM_ID,
        "exact_row_count": 4,
        "platform_read": bool(platform_read),
        "platform_write": bool(submitted),
        "account_action": bool(submitted),
        "price_change": False,
        "sku_rotation": False,
        "official_signup": False,
        "withdraw_pause_remove": False,
        "touches_existing_384_rows": False,
        "touches_plan8": False,
        "notification": False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, *, platform_read: bool = False,
          submitted: bool = False, **detail) -> dict:
    return {
        "ok": False,
        "error": error,
        **detail,
        "execution_boundary": _execution_boundary(
            platform_read=platform_read, submitted=submitted),
    }


def _load_attempt(db: Session) -> dict | None:
    raw = settings_service.get(db, ATTEMPT_KEY, env_fallback=False)
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
        description="计划7单品立减4行差量一次性CAS/回读回执（不含凭据）",
    )


def _expected_missing_rows() -> list[dict[str, str]]:
    return [{
        "item_id": row["item_id"],
        "sku_id": row["sku_id"],
        "expected_deduct": row["deduct"],
    } for row in EXPECTED_ROWS]


def _current_rows(db: Session, plan: CampaignPlan) -> tuple[list[dict], list[dict]]:
    rows, _stats = campaign_service.build_discount_rows(db, plan)
    selected = [row for row in rows if (
        str(row.get("taobao_item_id") or "") == EXPECTED_ITEM_ID
        and str(row.get("taobao_sku_id") or "")
        in {expected["sku_id"] for expected in EXPECTED_ROWS}
    )]
    canonical = sorted(({
        "item_id": str(row.get("taobao_item_id") or ""),
        "sku_id": str(row.get("taobao_sku_id") or ""),
        "sku_code": str(row.get("sku_code") or ""),
        "daily": _money(row.get("calculation_base")),
        "deduct": _money(row.get("deduct")),
        "official": _money(row.get("official")),
        "final": _money(row.get("target_price")),
        "kind": str(row.get("kind") or ""),
        "concession": _money(row.get("concession") or 0),
    } for row in selected), key=lambda row: row["sku_id"])
    return selected, canonical


def _snapshot_guard(db: Session, plan: CampaignPlan) -> tuple[CampaignEvidenceSnapshot | None, dict | None]:
    snapshot = db.get(CampaignEvidenceSnapshot, EXPECTED_SNAPSHOT_ID)
    latest = db.execute(select(CampaignEvidenceSnapshot).where(
        CampaignEvidenceSnapshot.plan_id == PLAN_ID,
        CampaignEvidenceSnapshot.evidence_type == "single_item_discount_readback",
    ).order_by(CampaignEvidenceSnapshot.id.desc())).scalars().first()
    if snapshot is None or latest is None or latest.id != EXPECTED_SNAPSHOT_ID:
        return None, _fail("discount_correction_snapshot_cas_mismatch")
    if (
        snapshot.plan_id != PLAN_ID
        or snapshot.workflow_key != WORKFLOW_KEY
        or snapshot.evidence_type != "single_item_discount_readback"
        or snapshot.request_id != EXPECTED_SNAPSHOT_REQUEST_ID
        or snapshot.scope_sha256 != EXPECTED_FULL_SCOPE_SHA256
        or snapshot.artifact_sha256 != EXPECTED_SNAPSHOT_ARTIFACT_SHA256
        or snapshot.result_status != "differences"
        or snapshot.artifact_blob is None
        or hashlib.sha256(snapshot.artifact_blob).hexdigest()
        != EXPECTED_SNAPSHOT_ARTIFACT_SHA256
    ):
        return None, _fail("discount_correction_snapshot_identity_mismatch")
    rows = snapshot.rows if isinstance(snapshot.rows, list) else []
    missing = sorted(({
        "item_id": str(row.get("item_id") or ""),
        "sku_id": str(row.get("sku_id") or ""),
        "expected_deduct": _money(row.get("expected_deduct")),
    } for row in rows if row.get("classification") == "missing"),
        key=lambda row: row["sku_id"])
    present = [row for row in rows if row.get("classification") == "present_not_effective"]
    if (
        len(rows) != 388
        or len(present) != 384
        or missing != sorted(_expected_missing_rows(), key=lambda row: row["sku_id"])
        or any(
            _money(row.get("actual_deduct")) != _money(row.get("expected_deduct"))
            or str(row.get("status") or "") != "未开始"
            or EXPECTED_ACTIVITY_ID not in {
                str(value) for value in (row.get("activity_ids") or [])
            }
            for row in present
        )
    ):
        return None, _fail("discount_correction_snapshot_rows_mismatch")
    if plan.start_at is None or _now_shanghai() >= plan.start_at:
        return None, _fail(
            "discount_correction_window_already_started",
            plan_start_at=str(plan.start_at),
        )
    return snapshot, None


def _validate_platform_rows(result: dict, *, required_class: str) -> dict | None:
    if not isinstance(result, dict):
        return _fail(
            "discount_correction_readback_invalid_response",
            platform_read=True,
        )
    if not result.get("ok"):
        return _fail(
            result.get("error") or "discount_correction_readback_failed",
            platform_read=True,
            web_agent_job_id=result.get("web_agent_job_id"),
        )
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    expected = sorted(_expected_missing_rows(), key=lambda row: row["sku_id"])
    actual = sorted(({
        "item_id": str(row.get("item_id") or ""),
        "sku_id": str(row.get("sku_id") or ""),
        "expected_deduct": _money(row.get("expected_deduct")),
    } for row in rows), key=lambda row: row["sku_id"])
    if actual != expected or len(rows) != 4:
        return _fail(
            "discount_correction_readback_scope_mismatch",
            platform_read=True,
            actual_rows=len(rows),
        )
    if required_class == "missing":
        if all(row.get("classification") == "missing" for row in rows):
            return None
    else:
        if all(
            row.get("classification") == required_class
            and _money(row.get("actual_deduct")) == _money(row.get("expected_deduct"))
            and str(row.get("status") or "") == "未开始"
            for row in rows
        ):
            return None
    return _fail(
        "discount_correction_platform_state_not_allowed",
        platform_read=True,
        required_class=required_class,
        rows=rows,
    )


def _platform_rows_are_exact(result: dict, classification: str) -> bool:
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    expected = sorted(_expected_missing_rows(), key=lambda row: row["sku_id"])
    actual = sorted(({
        "item_id": str(row.get("item_id") or ""),
        "sku_id": str(row.get("sku_id") or ""),
        "expected_deduct": _money(row.get("expected_deduct")),
    } for row in rows), key=lambda row: row["sku_id"])
    return (
        result.get("ok") is True
        and actual == expected
        and len(rows) == 4
        and all(
            row.get("classification") == classification
            and _money(row.get("actual_deduct")) == _money(row.get("expected_deduct"))
            and str(row.get("status") or "") == "未开始"
            for row in rows
        )
    )


def _platform_read(db: Session, plan: CampaignPlan) -> dict:
    scope = _expected_missing_rows()
    return web_agent_service.audit_plan7_single_discount(
        db,
        workflow_key=WORKFLOW_KEY,
        scope=scope,
        scope_sha256=EXPECTED_MISSING_SCOPE_SHA256,
        start_at=plan.start_at.strftime("%Y-%m-%d %H:%M:%S"),
        end_at=plan.end_at.strftime("%Y-%m-%d %H:%M:%S"),
    )


def correct_plan7_single_discount(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_snapshot_id: int, expected_snapshot_artifact_sha256: str,
        expected_missing_scope_sha256: str) -> dict:
    """Claim, submit exactly four rows once, then read them back exactly once."""
    if (
        workflow_key != WORKFLOW_KEY
        or expected_plan_id != PLAN_ID
        or expected_snapshot_id != EXPECTED_SNAPSHOT_ID
        or expected_snapshot_artifact_sha256 != EXPECTED_SNAPSHOT_ARTIFACT_SHA256
        or expected_missing_scope_sha256 != EXPECTED_MISSING_SCOPE_SHA256
    ):
        return _fail("discount_correction_request_not_allowed")
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    if (
        plan.status != EXPECTED_STATUS
        or plan.campaign_type != "super_reduce"
        or plan.platform_activity_mode != "long_running_update"
        or str(plan.qn_campaign_title or "").strip() != "超级立减"
    ):
        return _fail(
            "discount_correction_plan_identity_not_allowed",
            plan_status=plan.status,
        )
    snapshot, snapshot_error = _snapshot_guard(db, plan)
    if snapshot_error:
        return snapshot_error
    raw_rows, canonical = _current_rows(db, plan)
    expected_canonical = sorted(({
        **row, "kind": "nosales", "concession": "0.00"
    } for row in EXPECTED_ROWS), key=lambda row: row["sku_id"])
    if canonical != expected_canonical or len(raw_rows) != 4:
        return _fail(
            "discount_correction_erp_price_scope_drift",
            actual_rows=canonical,
        )
    if any(
        Decimal(row["daily"]) - Decimal(row["official"])
        - Decimal(row["deduct"]) != Decimal(row["final"])
        for row in canonical
    ):
        return _fail("discount_correction_final_price_math_mismatch")
    attempt = _load_attempt(db)
    if attempt:
        if attempt.get("status") == "completed":
            return {
                "ok": True,
                "idempotent_replay": True,
                "workflow_key": WORKFLOW_KEY,
                "plan_id": PLAN_ID,
                "attempt": attempt,
                "prior_platform_write": bool(attempt.get("submitted")),
                "execution_boundary": _execution_boundary(),
            }
        return _fail(
            "discount_correction_attempt_already_claimed_no_retry",
            attempt_id=attempt.get("attempt_id"),
            attempt_status=attempt.get("status"),
        )
    # Read-only freshness comes before the irreversible one-shot claim.  A
    # login/network failure here may be retried safely because no platform
    # write and no attempt claim has happened yet.
    db.commit()
    pre_read = _platform_read(db, plan)
    pre_error = _validate_platform_rows(pre_read, required_class="missing")
    if pre_error:
        # Exact rows already present at the exact amount is a successful noop,
        # not a reason to write them again.
        already_exact = _platform_rows_are_exact(
            pre_read, "present_not_effective")
        if not already_exact:
            return pre_error
        attempt_id = secrets.token_hex(12)
        final_attempt = {
            "status": "completed",
            "attempt_id": attempt_id,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "workflow_key": WORKFLOW_KEY,
            "plan_id": PLAN_ID,
            "snapshot_id": snapshot.id,
            "snapshot_request_id": snapshot.request_id,
            "snapshot_artifact_sha256": EXPECTED_SNAPSHOT_ARTIFACT_SHA256,
            "missing_scope_sha256": EXPECTED_MISSING_SCOPE_SHA256,
            "rows": canonical,
            "submitted": False,
            "pre_submit_web_agent_job_id": pre_read.get("web_agent_job_id"),
            "result_error": None,
            "automatic_retry": False,
        }
        _save_attempt(db, final_attempt)
        db.commit()
        return {
            "ok": True,
            "already_exact_no_write": True,
            "workflow_key": WORKFLOW_KEY,
            "plan_id": PLAN_ID,
            "attempt": final_attempt,
            "execution_boundary": _execution_boundary(platform_read=True),
        }

    # Re-acquire the row lock after the read-only platform check and repeat the
    # CAS guards so no concurrent worker can slip between evidence and claim.
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one()
    if _load_attempt(db):
        return _fail("discount_correction_attempt_raced_no_write")
    snapshot, snapshot_error = _snapshot_guard(db, plan)
    if snapshot_error:
        return snapshot_error
    raw_rows, canonical_after_read = _current_rows(db, plan)
    if canonical_after_read != canonical or len(raw_rows) != 4:
        return _fail("discount_correction_erp_scope_changed_after_read")
    attempt_id = secrets.token_hex(12)
    claimed = {
        "status": "claimed",
        "attempt_id": attempt_id,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "snapshot_id": snapshot.id,
        "snapshot_request_id": snapshot.request_id,
        "snapshot_artifact_sha256": EXPECTED_SNAPSHOT_ARTIFACT_SHA256,
        "missing_scope_sha256": EXPECTED_MISSING_SCOPE_SHA256,
        "rows": canonical,
        "pre_submit_web_agent_job_id": pre_read.get("web_agent_job_id"),
        "automatic_retry": False,
    }
    _save_attempt(db, claimed)
    db.commit()

    target_xlsx = campaign_service._build_discount_xlsx(raw_rows)
    if (
        campaign_discount_audit_service.xlsx_scope_sha256(target_xlsx)
        != EXPECTED_MISSING_SCOPE_SHA256
    ):
        failed = {
            **claimed,
            "status": "failed_target_xlsx_drift",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "submitted": False,
        }
        _save_attempt(db, failed)
        db.commit()
        return _fail(
            "discount_correction_target_xlsx_drift",
            platform_read=True,
            attempt=failed,
        )
    try:
        terminal = campaign_service._upload_and_wait(
            db,
            "single_item_discount",
            "commit",
            target_xlsx,
            plan.start_at.strftime("%Y-%m-%d %H:%M:%S"),
            plan.end_at.strftime("%Y-%m-%d %H:%M:%S"),
            plan=plan,
            expected_rows=4,
            ignore_plan_discount_activity=True,
        )
    except Exception as exc:  # noqa: BLE001 - outcome may be unknown
        failed = {
            **claimed,
            "status": "failed_unknown_outcome",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "submitted": None,
            "error_type": type(exc).__name__,
        }
        _save_attempt(db, failed)
        db.commit()
        return _fail(
            "discount_correction_unknown_outcome_no_retry",
            platform_read=True,
            attempt=failed,
        )
    submitted = bool(terminal.get("submitted"))
    if not terminal.get("ok"):
        failed = {
            **claimed,
            "status": "failed_terminal_no_retry",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "submitted": submitted,
            "terminal_job_id": terminal.get("job"),
            "terminal_error": terminal.get("error"),
            "terminal_evidence_request_id": terminal.get("evidence_request_id"),
        }
        _save_attempt(db, failed)
        db.commit()
        return _fail(
            "discount_correction_terminal_failed_no_retry",
            platform_read=True,
            submitted=submitted,
            attempt=failed,
            terminal=terminal,
        )

    post_read = _platform_read(db, plan)
    post_error = _validate_platform_rows(
        post_read, required_class="present_not_effective")
    artifact = post_read.get("artifact") if isinstance(post_read.get("artifact"), dict) else {}
    post_snapshot_id = None
    if post_error is None and (
        not artifact.get("content_b64")
        or not artifact.get("sha256")
        or not artifact.get("size")
    ):
        post_error = _fail(
            "discount_correction_post_submit_artifact_incomplete",
            platform_read=True,
            submitted=True,
        )
    if post_error is None:
        try:
            receipt = campaign_discount_audit_service._persist(
                db,
                plan=plan,
                evidence_type="single_item_discount_correction_readback",
                request_id=f"plan7-discount-correction-{secrets.token_hex(6)}",
                web_agent_job_id=post_read.get("web_agent_job_id"),
                scope_digest=EXPECTED_MISSING_SCOPE_SHA256,
                status="complete",
                summary=post_read.get("platform_summary"),
                rows=post_read.get("rows"),
                failure_rows=[],
                boundary=_execution_boundary(platform_read=True),
                artifact=artifact,
            )
            post_snapshot_id = receipt.id
        except Exception as exc:  # noqa: BLE001 - write already happened
            post_error = _fail(
                "discount_correction_post_submit_receipt_persist_failed",
                platform_read=True,
                submitted=True,
                error_type=type(exc).__name__,
            )
    final_status = "completed" if post_error is None else "failed_post_submit_readback"
    final_attempt = {
        **claimed,
        "status": final_status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "submitted": True,
        "terminal_job_id": terminal.get("job"),
        "terminal_evidence_request_id": terminal.get("evidence_request_id"),
        "post_submit_web_agent_job_id": post_read.get("web_agent_job_id"),
        "post_submit_artifact_sha256": artifact.get("sha256"),
        "post_submit_snapshot_id": post_snapshot_id,
        "result_error": post_error.get("error") if post_error else None,
    }
    _save_attempt(db, final_attempt)
    db.commit()
    if post_error:
        return {
            **post_error,
            "submitted": True,
            "attempt": final_attempt,
            "terminal": terminal,
            "execution_boundary": _execution_boundary(
                platform_read=True, submitted=True),
        }
    return {
        "ok": True,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "attempt": final_attempt,
        "terminal": terminal,
        "post_submit_readback": {
            "snapshot_id": post_snapshot_id,
            "web_agent_job_id": post_read.get("web_agent_job_id"),
            "platform_summary": post_read.get("platform_summary"),
            "rows": post_read.get("rows"),
            "artifact": {key: value for key, value in artifact.items()
                         if key != "content_b64"},
        },
        "execution_boundary": _execution_boundary(
            platform_read=True, submitted=True),
    }

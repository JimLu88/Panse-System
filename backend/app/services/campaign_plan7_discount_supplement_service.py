"""One-shot add-product supplement for plan-7 single-item discounts.

The entry is deliberately fixed to one reviewed item, four physical SKUs and
one existing activity.  It cannot create a new activity, change a price or
retry a claimed platform write.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_discount_audit_service,
    campaign_service,
    web_agent_service,
)


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
ITEM_ID = "1007407909979"
FORBIDDEN_PLACEHOLDER_SKU_ID = "6169015583658"
ACTIVITY_IDS = ("143780562424", "143936811502", "143939511827")
TARGET_ACTIVITY_ID = "143939511827"
START_AT = "2026-09-01 00:00:00"
END_AT = "2026-09-05 23:59:59"
SCOPE_SHA256 = (
    "eedccf8dd8ea9a5de1305c135f479703e7116a6fa547c0716edff800fbdec2f9"
)
READONLY_ARTIFACT_SHA256 = (
    "e3eacb64d2e8ba3a15b1bc07dc0c20df970f3851240a40e6de3ebe7a06b2b85b"
)
OPERATION = "discount_supplement"
EXPECTED_ROWS = (
    {"item_id": ITEM_ID, "sku_id": "6240788711164",
     "expected_deduct": "3508.50"},
    {"item_id": ITEM_ID, "sku_id": "6228006543289",
     "expected_deduct": "3315.99"},
    {"item_id": ITEM_ID, "sku_id": "6228006543290",
     "expected_deduct": "3101.93"},
    {"item_id": ITEM_ID, "sku_id": "6228006543291",
     "expected_deduct": "2911.38"},
)
ACTIVITY_BUSINESS_FACTS = {
    "143780562424": {
        "activity_name": "单品立减0828",
        "created_at": "2026-08-28 18:50:45",
        "import_status": None,
    },
    "143936811502": {
        "activity_name": "单品立减0830",
        "created_at": "2026-08-30 15:15:54",
        "import_status": "全部导入失败",
    },
    "143939511827": {
        "activity_name": "单品立减0830",
        "created_at": "2026-08-30 16:12:03",
        "import_status": "导入成功",
    },
}


def _money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"


def _expected_rows() -> list[dict[str, str]]:
    return sorted((dict(row) for row in EXPECTED_ROWS),
                  key=lambda row: row["sku_id"])


def _boundary(*, platform_read: bool = False,
              platform_write: bool | None = False) -> dict:
    return {
        "plan7_only": True,
        "exact_item_id": ITEM_ID,
        "exact_target_activity_id": TARGET_ACTIVITY_ID,
        "exact_row_count": 4,
        "platform_read": bool(platform_read),
        "platform_write": platform_write,
        "account_action": platform_write,
        "price_change": False,
        "sku_rotation": False,
        "creates_activity": False,
        "withdraw_pause_remove": False,
        "touches_other_items": False,
        "touches_plan8": False,
        "notification": False,
        "automatic_retry": False,
    }


def _fail(error: str, *, platform_read: bool = False,
          platform_write: bool | None = False, **detail) -> dict:
    return {
        "ok": False,
        "error": error,
        **detail,
        "execution_boundary": _boundary(
            platform_read=platform_read, platform_write=platform_write),
    }


def _validate_request(payload: dict) -> bool:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return False
    normalized = sorted(({
        "item_id": str(row.get("item_id") or ""),
        "sku_id": str(row.get("sku_id") or ""),
        "expected_deduct": _money(row.get("expected_deduct")),
    } for row in rows if isinstance(row, dict)), key=lambda row: row["sku_id"])
    return {
        "workflow_key": str(payload.get("workflow_key") or ""),
        "plan_id": payload.get("plan_id"),
        "activity_ids": tuple(str(value) for value in (
            payload.get("activity_ids") or [])),
        "target_activity_id": str(payload.get("target_activity_id") or ""),
        "item_id": str(payload.get("item_id") or ""),
        "rows": normalized,
        "scope_sha256": str(payload.get("scope_sha256") or "").lower(),
        "readonly_artifact_sha256": str(
            payload.get("readonly_artifact_sha256") or "").lower(),
        "start_at": str(payload.get("start_at") or ""),
        "end_at": str(payload.get("end_at") or ""),
    } == {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "activity_ids": ACTIVITY_IDS,
        "target_activity_id": TARGET_ACTIVITY_ID,
        "item_id": ITEM_ID,
        "rows": _expected_rows(),
        "scope_sha256": SCOPE_SHA256,
        "readonly_artifact_sha256": READONLY_ARTIFACT_SHA256,
        "start_at": START_AT,
        "end_at": END_AT,
    }


def request_payload() -> dict:
    """Return the immutable public request used by the controlled CLI."""
    return {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "activity_ids": list(ACTIVITY_IDS),
        "target_activity_id": TARGET_ACTIVITY_ID,
        "item_id": ITEM_ID,
        "rows": _expected_rows(),
        "scope_sha256": SCOPE_SHA256,
        "readonly_artifact_sha256": READONLY_ARTIFACT_SHA256,
        "start_at": START_AT,
        "end_at": END_AT,
    }


def _get_plan(db: Session, *, lock: bool = False) -> CampaignPlan | None:
    statement = select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()


def _validate_plan(plan: CampaignPlan | None) -> dict | None:
    if plan is None:
        return _fail("workflow_not_found")
    if (
        plan.status != "alarmed"
        or plan.campaign_type != "super_reduce"
        or plan.platform_activity_mode != "long_running_update"
        or str(plan.qn_campaign_title or "").strip() != "超级立减"
        or not plan.start_at or not plan.end_at
        or plan.start_at.strftime("%Y-%m-%d %H:%M:%S") != START_AT
        or plan.end_at.strftime("%Y-%m-%d %H:%M:%S") != END_AT
    ):
        return _fail(
            "plan7_discount_supplement_plan_identity_drift",
            plan_status=getattr(plan, "status", None),
            plan_start_at=str(getattr(plan, "start_at", None)),
            plan_end_at=str(getattr(plan, "end_at", None)),
        )
    return None


def _build_target_xlsx(db: Session, plan: CampaignPlan) -> tuple[bytes | None, dict | None]:
    rows, _ = campaign_service.build_discount_rows(db, plan)
    expected_ids = {row["sku_id"] for row in EXPECTED_ROWS}
    selected = [row for row in rows if (
        str(row.get("taobao_item_id") or "") == ITEM_ID
        and str(row.get("taobao_sku_id") or "") in expected_ids
    )]
    canonical = sorted(({
        "item_id": str(row.get("taobao_item_id") or ""),
        "sku_id": str(row.get("taobao_sku_id") or ""),
        "expected_deduct": _money(row.get("deduct")),
        "is_placeholder": bool(row.get("is_placeholder")),
    } for row in selected), key=lambda row: row["sku_id"])
    if (len(selected) != 4
            or canonical != [{**row, "is_placeholder": False}
                              for row in _expected_rows()]
            or any(str(row.get("taobao_sku_id") or "")
                   == FORBIDDEN_PLACEHOLDER_SKU_ID for row in selected)):
        return None, _fail(
            "plan7_discount_supplement_erp_scope_drift",
            actual_rows=canonical,
        )
    content = campaign_service._build_discount_xlsx(selected)
    if campaign_discount_audit_service.xlsx_scope_sha256(content) != SCOPE_SHA256:
        return None, _fail("plan7_discount_supplement_xlsx_scope_drift")
    return content, None


def _validate_activity_rows(result: dict) -> dict | None:
    activities = result.get("activity_rows")
    if not isinstance(activities, list) or len(activities) != 3:
        return _fail("plan7_discount_supplement_activity_scope_not_exact",
                     platform_read=True)
    by_id = {str(row.get("activity_id") or ""): row
             for row in activities if isinstance(row, dict)}
    if set(by_id) != set(ACTIVITY_IDS):
        return _fail("plan7_discount_supplement_activity_scope_not_exact",
                     platform_read=True, activity_ids=sorted(by_id))
    for activity_id, expected in ACTIVITY_BUSINESS_FACTS.items():
        row = by_id[activity_id]
        text = str(row.get("row_text") or "")
        if (not row.get("identity_readable")
                or str(row.get("status") or "") not in {"进行中", "生效中"}
                or START_AT not in text or END_AT not in text
                or expected["activity_name"] not in text
                or "自选商品活动" not in text
                or "SKU级" not in text or "减钱" not in text
                or expected["created_at"] not in text
                or (expected["import_status"]
                    and expected["import_status"] not in text)):
            return _fail(
                "plan7_discount_supplement_activity_identity_drift",
                platform_read=True,
                activity_id=activity_id,
                status=row.get("status"),
            )
    return None


def _validate_platform_read(result: dict, *, after_submit: bool) -> dict | None:
    if not isinstance(result, dict) or not result.get("ok"):
        return _fail(
            str((result or {}).get("error")
                or "plan7_discount_supplement_readback_failed"),
            platform_read=True,
            web_agent_job_id=(result or {}).get("web_agent_job_id"),
        )
    boundary = result.get("execution_boundary") or {}
    if boundary.get("platform_write") is not False:
        return _fail("plan7_discount_supplement_readback_boundary_violation",
                     platform_read=True)
    activity_error = _validate_activity_rows(result)
    if activity_error:
        return activity_error
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    canonical = sorted(({
        "item_id": str(row.get("item_id") or ""),
        "sku_id": str(row.get("sku_id") or ""),
        "expected_deduct": _money(row.get("expected_deduct")),
    } for row in rows if isinstance(row, dict)), key=lambda row: row["sku_id"])
    if canonical != _expected_rows() or len(rows) != 4:
        return _fail("plan7_discount_supplement_readback_scope_drift",
                     platform_read=True, actual_rows=canonical)
    for row in rows:
        if after_submit:
            allowed = (
                row.get("classification") == "correct_effective"
                and _money(row.get("actual_deduct"))
                == _money(row.get("expected_deduct"))
                and str(row.get("status") or "") in {"进行中", "生效中"}
                and [str(value) for value in (row.get("activity_ids") or [])]
                == [TARGET_ACTIVITY_ID]
            )
        else:
            allowed = (
                row.get("classification") == "missing"
                and row.get("actual_deduct") is None
                and not (row.get("activity_ids") or [])
            )
        if not allowed:
            return _fail(
                "plan7_discount_supplement_platform_state_not_allowed",
                platform_read=True, after_submit=after_submit, row=row)
    return None


def _platform_read(db: Session, plan: CampaignPlan) -> dict:
    return web_agent_service.audit_plan7_single_discount(
        db,
        workflow_key=WORKFLOW_KEY,
        scope=_expected_rows(),
        scope_sha256=SCOPE_SHA256,
        start_at=START_AT,
        end_at=END_AT,
    )


def _existing_attempt(db: Session) -> CampaignExecutionAttempt | None:
    return db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
        CampaignExecutionAttempt.scope_sha256 == SCOPE_SHA256,
    ).with_for_update()).scalar_one_or_none()


def _terminal_exact(result: dict) -> bool:
    terminal = result.get("official_terminal") or result.get("final_import") or {}
    boundary = result.get("execution_boundary") or {}
    trigger = result.get("trigger") or {}
    submit = result.get("platform_submit") or {}
    validation = result.get("validation") or {}
    return (
        result.get("ok") is True
        and result.get("submitted") is True
        and trigger.get("activity_id") == TARGET_ACTIVITY_ID
        and trigger.get("action") == "添加商品"
        and submit.get("attempted") is True
        and submit.get("control") == "确认修改"
        and validation.get("ok") == 4
        and validation.get("failed") == 0
        and boundary.get("platform_write") is True
        and terminal.get("state") == "complete"
        and terminal.get("ok") == 4
        and terminal.get("failed") == 0
    )


def execute_plan7_discount_supplement(db: Session, *, request_payload: dict) -> dict:
    """Read, claim, append four rows once, then read back every SKU."""
    try:
        request_ok = _validate_request(request_payload)
    except (TypeError, ValueError, ArithmeticError):
        request_ok = False
    if not request_ok:
        return _fail("plan7_discount_supplement_request_not_allowed")
    plan = _get_plan(db)
    plan_error = _validate_plan(plan)
    if plan_error:
        return plan_error
    target_xlsx, xlsx_error = _build_target_xlsx(db, plan)
    if xlsx_error:
        return xlsx_error

    existing = _existing_attempt(db)
    if existing:
        if existing.state == "completed":
            return {
                "ok": True,
                "idempotent_replay": True,
                "attempt_id": existing.id,
                "state": existing.state,
                "result_summary": existing.result_summary,
                "execution_boundary": _boundary(),
            }
        return _fail(
            "plan7_discount_supplement_attempt_already_claimed_no_retry",
            attempt_id=existing.id,
            attempt_state=existing.state,
            platform_write=existing.platform_write_observed,
        )

    db.commit()
    pre_read = _platform_read(db, plan)
    pre_error = _validate_platform_read(pre_read, after_submit=False)
    if pre_error:
        return pre_error

    plan = _get_plan(db, lock=True)
    plan_error = _validate_plan(plan)
    if plan_error:
        return plan_error
    if _existing_attempt(db):
        return _fail("plan7_discount_supplement_claim_raced_no_write")
    target_xlsx_after, xlsx_error = _build_target_xlsx(db, plan)
    if xlsx_error:
        return xlsx_error
    # Excel package metadata (notably the generated/modified timestamp) may
    # legitimately change while the platform read is running.  `_build_target_xlsx`
    # has already revalidated all four semantic rows and their fixed scope digest;
    # comparing the raw ZIP bytes here would create a false drift alarm.
    target_xlsx = target_xlsx_after
    attempt_id = secrets.token_hex(12)
    request_id = f"plan7-discount-supplement-{secrets.token_hex(8)}"
    attempt = CampaignExecutionAttempt(
        id=attempt_id,
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        operation=OPERATION,
        scope_sha256=SCOPE_SHA256,
        state="write_claimed",
        write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc),
        platform_write_observed=False,
        automatic_retry_allowed=False,
        request_id=request_id,
        web_agent_job_id=pre_read.get("web_agent_job_id"),
        last_step="fresh_missing_readback_then_write_claimed",
        result_summary={
            "trigger": {
                "workflow_key": WORKFLOW_KEY,
                "plan_id": PLAN_ID,
                "item_id": ITEM_ID,
                "target_activity_id": TARGET_ACTIVITY_ID,
                "scope_sha256": SCOPE_SHA256,
                "readonly_artifact_sha256": READONLY_ARTIFACT_SHA256,
                "pre_read_job_id": pre_read.get("web_agent_job_id"),
            },
            "platform_submit": None,
            "official_terminal": None,
            "readback": None,
        },
    )
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = _existing_attempt(db)
        return _fail(
            "plan7_discount_supplement_claim_raced_no_write",
            attempt_id=getattr(raced, "id", None))

    xlsx_sha = hashlib.sha256(target_xlsx).hexdigest()
    web_payload = {
        **request_payload,
        "xlsx_sha256": xlsx_sha,
        "xlsx_b64": base64.b64encode(target_xlsx).decode("ascii"),
    }
    try:
        terminal = web_agent_service.supplement_plan7_single_discount(
            db, payload=web_payload)
    except Exception as exc:  # noqa: BLE001 - outcome is unknown after claim
        terminal = {
            "ok": False,
            "submitted": None,
            "error": f"{type(exc).__name__}: {exc}",
            "execution_boundary": {"platform_write": None},
        }
    observed = (terminal.get("execution_boundary") or {}).get("platform_write")
    if observed not in {True, False}:
        observed = None
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.state = "platform_terminal" if _terminal_exact(terminal) else (
        "failed" if observed is False else "unknown")
    attempt.platform_write_observed = observed
    attempt.web_agent_job_id = terminal.get("web_agent_job_id")
    attempt.last_step = "official_terminal" if _terminal_exact(terminal) else (
        "platform_outcome_not_exact")
    attempt.error_code = None if _terminal_exact(terminal) else str(
        terminal.get("error") or "plan7_discount_supplement_terminal_not_exact")[:128]
    attempt.result_summary = {
        **(attempt.result_summary or {}),
        "platform_submit": terminal.get("platform_submit"),
        "official_terminal": terminal.get("official_terminal")
        or terminal.get("final_import"),
        "terminal_job_id": terminal.get("web_agent_job_id"),
        "terminal_error": terminal.get("error"),
    }
    db.commit()
    terminal_receipt = campaign_discount_audit_service.persist_single_discount_terminal(
        plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        job_id=terminal.get("web_agent_job_id"),
        target_xlsx=target_xlsx, result=terminal)
    if not _terminal_exact(terminal):
        return _fail(
            "plan7_discount_supplement_terminal_not_exact_no_retry",
            platform_read=True,
            platform_write=observed,
            attempt_id=attempt_id,
            terminal_receipt_request_id=terminal_receipt,
            terminal=terminal,
        )

    post_read = _platform_read(db, plan)
    post_error = _validate_platform_read(post_read, after_submit=True)
    artifact = post_read.get("artifact") if isinstance(
        post_read.get("artifact"), dict) else {}
    snapshot_id = None
    if post_error is None and not all(
            artifact.get(key) for key in ("content_b64", "sha256", "size")):
        post_error = _fail(
            "plan7_discount_supplement_post_readback_artifact_incomplete",
            platform_read=True, platform_write=True)
    if post_error is None:
        snapshot = campaign_discount_audit_service._persist(
            db,
            plan=plan,
            evidence_type="plan7_discount_supplement_readback",
            request_id=f"plan7-discount-supplement-readback-{secrets.token_hex(6)}",
            web_agent_job_id=post_read.get("web_agent_job_id"),
            scope_digest=SCOPE_SHA256,
            status="complete",
            summary=post_read.get("platform_summary"),
            rows=post_read.get("rows"),
            failure_rows=[],
            boundary=_boundary(platform_read=True, platform_write=False),
            artifact=artifact,
        )
        snapshot_id = snapshot.id
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.state = "completed" if post_error is None else "failed"
    attempt.last_step = "readback_verified" if post_error is None else (
        "post_submit_readback_failed")
    attempt.error_code = None if post_error is None else str(
        post_error.get("error"))[:128]
    attempt.result_summary = {
        **(attempt.result_summary or {}),
        "terminal_receipt_request_id": terminal_receipt,
        "readback": {
            "ok": post_error is None,
            "job_id": post_read.get("web_agent_job_id"),
            "snapshot_id": snapshot_id,
            "artifact_sha256": artifact.get("sha256"),
            "rows": post_read.get("rows"),
            "error": post_error.get("error") if post_error else None,
        },
    }
    db.commit()
    if post_error:
        return {
            **post_error,
            "attempt_id": attempt_id,
            "submitted": True,
            "official_terminal": terminal.get("official_terminal"),
            "execution_boundary": _boundary(
                platform_read=True, platform_write=True),
        }
    return {
        "ok": True,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "attempt_id": attempt_id,
        "item_id": ITEM_ID,
        "activity_id": TARGET_ACTIVITY_ID,
        "trigger": (attempt.result_summary or {}).get("trigger"),
        "platform_submit": (attempt.result_summary or {}).get("platform_submit"),
        "official_terminal": (attempt.result_summary or {}).get("official_terminal"),
        "readback": (attempt.result_summary or {}).get("readback"),
        "execution_boundary": _boundary(
            platform_read=True, platform_write=True),
    }

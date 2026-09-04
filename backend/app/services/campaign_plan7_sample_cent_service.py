"""One-shot 5.99 single-discount repair for four exact sample SKUs.

The service keeps ERP/signup price at 30.00, never touches placeholder SKUs,
claims the platform write once, and requires both an official 4/0 terminal and
an independent per-SKU readback.
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
from app.models.pricing import PricingSku
from app.models.sku_identity import SkuIdentity
from app.services import (
    campaign_discount_audit_service,
    campaign_service,
    web_agent_service,
)


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
ITEM_ID = "719436834260"
ACTIVITY_IDS = ("143780562424", "143936811502", "143939511827")
TARGET_ACTIVITY_ID = "143939511827"
START_AT = "2026-09-01 00:00:00"
END_AT = "2026-09-05 23:59:59"
SCOPE_SHA256 = (
    "e2c8bfa1e3db32d0937971ea8481414baacc2d8f82e63484810168efc2f97fce"
)
READONLY_ARTIFACT_SHA256 = (
    "80a9d3d406e4936fe1c801c53fb2119edc752cdb52de32914f8dc3cc1e1cfc8a"
)
OPERATION = "plan7_sample_cent_discount"
DAILY_PRICE = Decimal("30.00")
EXPECTED_ROWS = (
    {"item_id": ITEM_ID, "sku_id": "6285733543660",
     "sku_code": "PPS2398001060611", "expected_deduct": "5.99"},
    {"item_id": ITEM_ID, "sku_id": "5024477897617",
     "sku_code": "PPS2398001060612", "expected_deduct": "5.99"},
    {"item_id": ITEM_ID, "sku_id": "6120623944056",
     "sku_code": "PPS2398001060613", "expected_deduct": "5.99"},
    {"item_id": ITEM_ID, "sku_id": "6282622238127",
     "sku_code": "PPS2398001060614", "expected_deduct": "5.99"},
)
FORBIDDEN_SKU_IDS = {
    "5012620812067", "5024477897619", "5024477897620",
    "5556333932522", "6278611018580", "6278614802119",
    "6278875759312",
}
ACTIVITY_BUSINESS_FACTS = {
    "143780562424": ("单品立减0828", "2026-08-28 18:50:45"),
    "143936811502": ("单品立减0830", "2026-08-30 15:15:54"),
    "143939511827": ("单品立减0830", "2026-08-30 16:12:03"),
}


def _money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"


def _expected_rows() -> list[dict[str, str]]:
    return sorted(({
        "item_id": row["item_id"],
        "sku_id": row["sku_id"],
        "expected_deduct": row["expected_deduct"],
    } for row in EXPECTED_ROWS), key=lambda row: row["sku_id"])


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
        "erp_daily_price_change": False,
        "signup_price_change": False,
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


def request_payload() -> dict:
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


def _validate_request(payload: dict) -> bool:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return False
    try:
        normalized = sorted(({
            "item_id": str(row.get("item_id") or ""),
            "sku_id": str(row.get("sku_id") or ""),
            "expected_deduct": _money(row.get("expected_deduct")),
        } for row in rows if isinstance(row, dict)),
            key=lambda row: row["sku_id"])
    except (ArithmeticError, TypeError, ValueError):
        return False
    return {
        "workflow_key": str(payload.get("workflow_key") or ""),
        "plan_id": payload.get("plan_id"),
        "activity_ids": [str(value) for value in (
            payload.get("activity_ids") or [])],
        "target_activity_id": str(payload.get("target_activity_id") or ""),
        "item_id": str(payload.get("item_id") or ""),
        "rows": normalized,
        "scope_sha256": str(payload.get("scope_sha256") or "").lower(),
        "readonly_artifact_sha256": str(
            payload.get("readonly_artifact_sha256") or "").lower(),
        "start_at": str(payload.get("start_at") or ""),
        "end_at": str(payload.get("end_at") or ""),
    } == request_payload()


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
        plan.status != "reconciled"
        or plan.campaign_type != "super_reduce"
        or plan.platform_activity_mode != "long_running_update"
        or str(plan.qn_campaign_title or "").strip() != "超级立减"
        or not plan.start_at or not plan.end_at
        or plan.start_at.strftime("%Y-%m-%d %H:%M:%S") != START_AT
        or plan.end_at.strftime("%Y-%m-%d %H:%M:%S") != END_AT
    ):
        return _fail(
            "plan7_sample_cent_plan_identity_drift",
            plan_status=getattr(plan, "status", None),
            plan_start_at=str(getattr(plan, "start_at", None)),
            plan_end_at=str(getattr(plan, "end_at", None)),
        )
    return None


def _erp_scope(db: Session) -> tuple[list[dict] | None, dict | None]:
    expected_by_sku = {row["sku_id"]: row for row in EXPECTED_ROWS}
    identities = db.execute(select(SkuIdentity).where(
        SkuIdentity.taobao_item_id == ITEM_ID,
        SkuIdentity.taobao_sku_id.in_(expected_by_sku),
    )).scalars().all()
    by_sku = {row.taobao_sku_id: row for row in identities}
    pricing = db.execute(select(PricingSku).where(
        PricingSku.sku_code.in_({row["sku_code"] for row in EXPECTED_ROWS})
    )).scalars().all()
    by_code = {row.sku_code: row for row in pricing}
    facts: list[dict] = []
    for sku_id, expected in expected_by_sku.items():
        identity = by_sku.get(sku_id)
        price = by_code.get(expected["sku_code"])
        fact = {
            "item_id": ITEM_ID,
            "sku_id": sku_id,
            "expected_sku_code": expected["sku_code"],
            "identity_sku_code": getattr(identity, "sku_code", None),
            "identity_merchant_code": getattr(identity, "merchant_code", None),
            "identity_product_code": getattr(identity, "product_code", None),
            "identity_daily_price": (
                _money(identity.latest_daily_price)
                if identity is not None and identity.latest_daily_price is not None
                else None),
            "identity_placeholder": getattr(
                identity, "is_custom_placeholder", None),
            "identity_conflict": getattr(identity, "conflict_detected", None),
            "pricing_product_code": getattr(price, "product_code", None),
            "pricing_daily_price": (
                _money(price.daily_price)
                if price is not None and price.daily_price is not None else None),
            "pricing_placeholder": getattr(price, "is_custom_placeholder", None),
        }
        facts.append(fact)
        if (
            identity is None or price is None
            or identity.sku_code != expected["sku_code"]
            or identity.merchant_code != expected["sku_code"]
            or identity.product_code != price.product_code
            or identity.is_custom_placeholder is not False
            or identity.conflict_detected is not False
            or identity.latest_daily_price != DAILY_PRICE
            or price.daily_price != DAILY_PRICE
            or price.is_custom_placeholder is not False
        ):
            return None, _fail(
                "plan7_sample_cent_erp_identity_or_price_drift",
                actual_facts=facts)
    if set(by_sku) != set(expected_by_sku) or set(by_code) != {
            row["sku_code"] for row in EXPECTED_ROWS}:
        return None, _fail(
            "plan7_sample_cent_erp_identity_or_price_drift",
            actual_facts=facts)
    return sorted(facts, key=lambda row: row["sku_id"]), None


def _build_target_xlsx(db: Session) -> tuple[bytes | None, list[dict] | None,
                                                 dict | None]:
    facts, error = _erp_scope(db)
    if error:
        return None, None, error
    rows = [{
        "taobao_item_id": row["item_id"],
        "taobao_sku_id": row["sku_id"],
        "deduct": "5.99",
    } for row in _expected_rows()]
    if ({row["taobao_sku_id"] for row in rows} & FORBIDDEN_SKU_IDS):
        return None, None, _fail("plan7_sample_cent_forbidden_sku_present")
    content = campaign_service._build_discount_xlsx(rows)
    if campaign_discount_audit_service.xlsx_scope_sha256(content) != SCOPE_SHA256:
        return None, None, _fail("plan7_sample_cent_xlsx_scope_drift")
    return content, facts, None


def _validate_activity_rows(result: dict) -> dict | None:
    activities = result.get("activity_rows")
    if not isinstance(activities, list) or len(activities) != 3:
        return _fail("plan7_sample_cent_activity_scope_not_exact",
                     platform_read=True)
    by_id = {str(row.get("activity_id") or ""): row
             for row in activities if isinstance(row, dict)}
    if set(by_id) != set(ACTIVITY_IDS):
        return _fail("plan7_sample_cent_activity_scope_not_exact",
                     platform_read=True, activity_ids=sorted(by_id))
    for activity_id, (activity_name, created_at) in ACTIVITY_BUSINESS_FACTS.items():
        row = by_id[activity_id]
        text = str(row.get("row_text") or "")
        if (
            not row.get("identity_readable")
            or str(row.get("status") or "") not in {"进行中", "生效中"}
            or START_AT not in text or END_AT not in text
            or activity_name not in text or created_at not in text
            or "自选商品活动" not in text
            or "SKU级" not in text or "减钱" not in text
            or "添加商品" not in text
        ):
            return _fail(
                "plan7_sample_cent_activity_identity_drift",
                platform_read=True,
                activity_id=activity_id,
                status=row.get("status"),
            )
    return None


def _artifact_integrity(result: dict, *, require_frozen_sha: bool) -> dict | None:
    artifact = result.get("artifact")
    if not isinstance(artifact, dict):
        return _fail("plan7_sample_cent_readback_artifact_incomplete",
                     platform_read=True)
    try:
        raw = base64.b64decode(artifact.get("content_b64") or "", validate=True)
    except Exception:
        raw = b""
    digest = hashlib.sha256(raw).hexdigest() if raw else None
    if (
        not raw
        or artifact.get("size") != len(raw)
        or artifact.get("sha256") != digest
        or (require_frozen_sha and digest != READONLY_ARTIFACT_SHA256)
    ):
        return _fail(
            "plan7_sample_cent_readback_artifact_drift",
            platform_read=True,
            expected_sha256=(READONLY_ARTIFACT_SHA256
                             if require_frozen_sha else None),
            actual_sha256=digest,
        )
    return None


def _validate_platform_read(result: dict, *, after_submit: bool) -> dict | None:
    if not isinstance(result, dict) or not result.get("ok"):
        return _fail(
            str((result or {}).get("error")
                or "plan7_sample_cent_readback_failed"),
            platform_read=True,
            web_agent_job_id=(result or {}).get("web_agent_job_id"),
        )
    if (result.get("execution_boundary") or {}).get("platform_write") is not False:
        return _fail("plan7_sample_cent_readback_boundary_violation",
                     platform_read=True)
    activity_error = _validate_activity_rows(result)
    if activity_error:
        return activity_error
    artifact_error = _artifact_integrity(
        result, require_frozen_sha=not after_submit)
    if artifact_error:
        return artifact_error
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    canonical = sorted(({
        "item_id": str(row.get("item_id") or ""),
        "sku_id": str(row.get("sku_id") or ""),
        "expected_deduct": _money(row.get("expected_deduct")),
    } for row in rows if isinstance(row, dict)), key=lambda row: row["sku_id"])
    if canonical != _expected_rows() or len(rows) != 4:
        return _fail("plan7_sample_cent_readback_scope_drift",
                     platform_read=True, actual_rows=canonical)
    for row in rows:
        if after_submit:
            allowed = (
                row.get("classification") == "correct_effective"
                and _money(row.get("actual_deduct")) == "5.99"
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
                "plan7_sample_cent_platform_state_not_allowed",
                platform_read=True, after_submit=after_submit, row=row)
    return None


def _platform_read(db: Session, plan: CampaignPlan) -> dict:
    return web_agent_service.audit_plan7_single_discount(
        db, workflow_key=WORKFLOW_KEY, scope=_expected_rows(),
        scope_sha256=SCOPE_SHA256, start_at=START_AT, end_at=END_AT)


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
        and validation.get("ok") == 4 and validation.get("failed") == 0
        and boundary.get("platform_write") is True
        and terminal.get("state") == "complete"
        and terminal.get("ok") == 4 and terminal.get("failed") == 0
    )


def execute_plan7_sample_cent(db: Session, *, request_payload: dict) -> dict:
    """Read, claim, write four rows once, then prove every row effective."""
    if not _validate_request(request_payload):
        return _fail("plan7_sample_cent_request_not_allowed")
    plan = _get_plan(db)
    plan_error = _validate_plan(plan)
    if plan_error:
        return plan_error
    existing = _existing_attempt(db)
    if existing:
        return _fail(
            "plan7_sample_cent_retired_after_success"
            if existing.state == "completed"
            else "plan7_sample_cent_attempt_already_claimed_no_retry",
            attempt_id=existing.id, attempt_state=existing.state,
            platform_write=existing.platform_write_observed)
    target_xlsx, erp_facts, xlsx_error = _build_target_xlsx(db)
    if xlsx_error:
        return xlsx_error

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
        return _fail("plan7_sample_cent_claim_raced_no_write")
    target_xlsx, erp_facts_after, xlsx_error = _build_target_xlsx(db)
    if xlsx_error:
        return xlsx_error
    if erp_facts_after != erp_facts:
        return _fail("plan7_sample_cent_erp_scope_changed_during_read")

    attempt_id = secrets.token_hex(12)
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
        request_id=f"plan7-sample-cent-{secrets.token_hex(8)}",
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
            "erp_facts": erp_facts,
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
        return _fail("plan7_sample_cent_claim_raced_no_write",
                     attempt_id=getattr(raced, "id", None))

    web_payload = {
        **request_payload,
        "xlsx_sha256": hashlib.sha256(target_xlsx).hexdigest(),
        "xlsx_b64": base64.b64encode(target_xlsx).decode("ascii"),
    }
    try:
        terminal = web_agent_service.supplement_plan7_sample_cent_single_discount(
            db, payload=web_payload)
    except Exception as exc:  # outcome is unknown after the durable claim
        terminal = {
            "ok": False, "submitted": None,
            "error": f"{type(exc).__name__}: {exc}",
            "execution_boundary": {"platform_write": None},
        }
    observed = (terminal.get("execution_boundary") or {}).get("platform_write")
    if observed not in {True, False}:
        observed = None
    terminal_ok = _terminal_exact(terminal)
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.state = "platform_terminal" if terminal_ok else (
        "failed" if observed is False else "unknown")
    attempt.platform_write_observed = observed
    attempt.web_agent_job_id = terminal.get("web_agent_job_id")
    attempt.last_step = "official_terminal" if terminal_ok else (
        "platform_outcome_not_exact")
    attempt.error_code = None if terminal_ok else str(
        terminal.get("error") or "plan7_sample_cent_terminal_not_exact")[:128]
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
    if not terminal_ok:
        return _fail(
            "plan7_sample_cent_terminal_not_exact_no_retry",
            platform_read=True, platform_write=observed,
            attempt_id=attempt_id,
            terminal_receipt_request_id=terminal_receipt,
            terminal=terminal)

    post_read = _platform_read(db, plan)
    post_error = _validate_platform_read(post_read, after_submit=True)
    artifact = post_read.get("artifact") if isinstance(
        post_read.get("artifact"), dict) else {}
    snapshot_id = None
    if post_error is None:
        snapshot = campaign_discount_audit_service._persist(
            db, plan=plan,
            evidence_type="plan7_sample_cent_discount_readback",
            request_id=f"plan7-sample-cent-readback-{secrets.token_hex(6)}",
            web_agent_job_id=post_read.get("web_agent_job_id"),
            scope_digest=SCOPE_SHA256,
            status="complete",
            summary=post_read.get("platform_summary"),
            rows=post_read.get("rows"), failure_rows=[],
            boundary=_boundary(platform_read=True, platform_write=False),
            artifact=artifact)
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

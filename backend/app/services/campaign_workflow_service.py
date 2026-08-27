"""Formal ERP-internal campaign preparation service.

This service owns ERP data selection, no-sales grouping, row generation and
preflight.  It never controls a browser, uploads to QianNiu, or writes platform
state.  ``workflow_key`` is the durable idempotency boundary across restarts.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignPlan
from app.services import campaign_service


_IDENTITY_FIELDS = (
    "name", "campaign_type", "tier", "start_at", "end_at",
    "qn_campaign_title", "price_protection_days", "price_protection_rule_url",
    "remark", "platform_activity_mode", "platform_campaign_id",
    "platform_united_activity_id", "platform_active_until",
)


def _different_fields(plan: CampaignPlan, values: dict) -> list[str]:
    return [
        field for field in _IDENTITY_FIELDS
        if getattr(plan, field, None) != values.get(field)
    ]


def _remark_segments(value: str | None) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(r"[;\n；]", str(value or ""))
        if segment.strip()
    ]


def _is_empty_exempt_marker_enrichment(
        plan: CampaignPlan, values: dict, different: list[str]) -> bool:
    """Allow one safe repair for plans created before explicit [] persisted.

    The durable workflow identity remains immutable.  We only enrich a draft
    or precheck plan when every other identity field is identical and the new
    remark is byte-for-byte the old segments plus exactly one empty
    ``official_exempt_items=`` marker.  Any non-empty exemption, free-text
    change, or other identity change still conflicts.
    """
    if different != ["remark"] or plan.status not in ("draft", "precheck"):
        return False
    old_segments = _remark_segments(plan.remark)
    new_segments = _remark_segments(values.get("remark"))
    if not any(re.fullmatch(
            r"official_all_store\s*=\s*(?:true|1|yes|on)",
            segment, flags=re.IGNORECASE) for segment in old_segments):
        return False
    empty_markers = [
        segment for segment in new_segments
        if re.fullmatch(
            r"official_exempt_items\s*=\s*", segment, flags=re.IGNORECASE)
    ]
    if len(empty_markers) != 1:
        return False
    remaining = [segment for segment in new_segments if segment not in empty_markers]
    return remaining == old_segments


def _package_existing(
        db: Session, plan: CampaignPlan, *, created: bool,
        repaired_fields: list[str] | None = None) -> dict:
    """Build the formal ERP package without performing any platform write."""
    grouping = campaign_service.group_by_sales(db)
    signup_rows, signup_stats = campaign_service.build_signup_rows(db, plan)
    discount_rows, discount_stats = campaign_service.build_discount_rows(db, plan)
    checks = campaign_service.preflight(db, plan)
    blocking = [check for check in checks if check.get("level") == "error"]
    if plan.status == "draft" and not blocking:
        plan.status = "precheck"
        db.commit()
    return {
        "ok": True,
        "created": created,
        "reused": not created,
        "repaired_fields": repaired_fields or [],
        "workflow_key": plan.workflow_key,
        "plan": plan,
        "grouping": grouping,
        "signup": {"rows": signup_rows, "stats": signup_stats},
        "discount": {"rows": discount_rows, "stats": discount_stats},
        "preflight": {"checks": checks, "has_error": bool(blocking)},
        "execution_boundary": {
            "erp_source": "formal_backend_services",
            "browser_reads_erp_pages": False,
            "platform_write": False,
            "account_action": False,
            "notification": False,
            "automatic_retry": False,
            "allowed_next_browser_scope": (
                "external_platform_login_discovery_upload_submit_and_official_receipt_only"
            ),
        },
    }


def prepare(db: Session, *, workflow_key: str, values: dict) -> dict:
    """Create/reuse one plan and return a fresh structured read-only package."""
    existing = db.execute(select(CampaignPlan).where(
        CampaignPlan.workflow_key == workflow_key)).scalar_one_or_none()
    created = existing is None
    repaired_fields: list[str] = []
    if created:
        plan = CampaignPlan(workflow_key=workflow_key, status="draft", **values)
        db.add(plan)
        db.commit()
        db.refresh(plan)
    else:
        plan = existing
        different = _different_fields(plan, values)
        if _is_empty_exempt_marker_enrichment(plan, values, different):
            plan.remark = values["remark"]
            db.commit()
            db.refresh(plan)
            repaired_fields = ["remark"]
        elif different:
            return {
                "ok": False,
                "conflict": True,
                "workflow_key": workflow_key,
                "plan": plan,
                "different_fields": different,
            }

    return _package_existing(
        db, plan, created=created, repaired_fields=repaired_fields)


def refresh_evidence_and_prepare(
        db: Session, *, workflow_key: str,
        expected_plan_id: int | None = None) -> dict:
    """Refresh one existing plan's read-only QianNiu export, then preflight.

    This deliberately has no retry, notification, signup, upload, price-change,
    withdrawal or submission branch.  The caller gets the export fingerprint
    and the full formal ERP package for the same durable workflow identity.
    """
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.workflow_key == workflow_key)).scalar_one_or_none()
    if plan is None:
        return {"ok": False, "error": "workflow_not_found"}
    if expected_plan_id is not None and plan.id != expected_plan_id:
        return {
            "ok": False,
            "error": "workflow_plan_mismatch",
            "plan_id": plan.id,
        }
    refreshed = campaign_service.refresh_floor_evidence_from_current_activity(
        db, plan)
    if not refreshed.get("ok"):
        db.rollback()
        return {
            "ok": False,
            "error": refreshed.get("error") or "evidence_refresh_failed",
            "step": refreshed.get("step") or "current_activity_export",
            "plan_id": plan.id,
            "workflow_key": workflow_key,
            "job_id": refreshed.get("job_id"),
            "detail": refreshed.get("detail"),
            "execution_boundary": {
                "platform_read": "current_activity_export_only",
                "platform_write": False,
                "account_action": False,
                "notification": False,
                "automatic_retry": False,
            },
        }
    # The export is independently valuable read-only evidence and must remain
    # durable even when R16/R17 intentionally keep the package blocked.
    db.commit()
    package = _package_existing(db, plan, created=False)
    by_rule = {
        check["rule"]: check for check in package["preflight"]["checks"]
    }
    package.update({
        "plan_id": plan.id,
        "floor_refresh": refreshed.get("floor_refresh"),
        "placeholder_price_refresh": refreshed.get("placeholder_price_refresh"),
        "export_evidence": refreshed.get("export_evidence"),
        "gate_results": {
            "R16": by_rule.get("R16"),
            "R17": by_rule.get("R17"),
        },
    })
    package["execution_boundary"].update({
        "platform_read": "current_activity_export_only",
        "platform_write": False,
        "account_action": False,
        "notification": False,
        "automatic_retry": False,
    })
    return package

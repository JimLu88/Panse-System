"""Formal ERP-internal campaign preparation service.

This service owns ERP data selection, no-sales grouping, row generation and
preflight.  It never controls a browser, uploads to QianNiu, or writes platform
state.  ``workflow_key`` is the durable idempotency boundary across restarts.
"""
from __future__ import annotations

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


def prepare(db: Session, *, workflow_key: str, values: dict) -> dict:
    """Create/reuse one plan and return a fresh structured read-only package."""
    existing = db.execute(select(CampaignPlan).where(
        CampaignPlan.workflow_key == workflow_key)).scalar_one_or_none()
    created = existing is None
    if created:
        plan = CampaignPlan(workflow_key=workflow_key, status="draft", **values)
        db.add(plan)
        db.commit()
        db.refresh(plan)
    else:
        plan = existing
        different = _different_fields(plan, values)
        if different:
            return {
                "ok": False,
                "conflict": True,
                "workflow_key": workflow_key,
                "plan": plan,
                "different_fields": different,
            }

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
        "workflow_key": workflow_key,
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
            "allowed_next_browser_scope": (
                "external_platform_login_discovery_upload_submit_and_official_receipt_only"
            ),
        },
    }

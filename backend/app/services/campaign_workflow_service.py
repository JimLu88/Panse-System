"""Formal ERP-internal campaign preparation service.

This service owns ERP data selection, no-sales grouping, row generation and
preflight.  It never controls a browser, uploads to QianNiu, or writes platform
state.  ``workflow_key`` is the durable idempotency boundary across restarts.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignPlan, CampaignReconReport
from app.services import campaign_service


_IDENTITY_FIELDS = (
    "name", "campaign_type", "tier", "start_at", "end_at",
    "qn_campaign_title", "price_protection_days", "price_protection_rule_url",
    "remark", "platform_activity_mode", "platform_campaign_id",
    "platform_united_activity_id", "platform_sign_record_id",
    "platform_active_until",
)


# ``CampaignPlan.remark`` predates the formal preparation API and still carries
# both request-owned scope and runtime evidence.  These keys are written by the
# campaign engine after preparation and therefore must not make an unchanged
# workflow payload conflict.  They are never removed by ``prepare``; only the
# comparison projection ignores them.
_RUNTIME_REMARK_KEYS = {
    "campaignid",
    "unitedactivityid",
    "current_activity_prices",
    "line_concession_authorized",
    "placeholder_live_prices",
    "placeholder_price_lowering_authorized",
    "placeholder_price_protection_expired",
    "platform_existing_wrong_items",
    "platform_hard_failed_items",
    "platform_no_sales_items",
    "platform_qualified_items",
    "signup_shipping_days_authorized",
    "single_discount_activity_id",
    "single_discount_activity_ids",
    "single_discount_refreshed_activity_ids",
    "single_discount_sku_activity_ids",
    "sku_refresh_items_authorized",
    "supplement_items_authorized",
    "super_reduce_early_activation_already_clear",
    "super_reduce_early_activation_withdrawn",
}
_RUNTIME_REMARK_PREFIXES = (
    "platform_terminal_accepted_",
    "platform_terminal_coupon_floor_",
)
_PLATFORM_WRITE_REMARK_KEYS = {
    "single_discount_activity_id",
    "single_discount_activity_ids",
    "single_discount_refreshed_activity_ids",
    "single_discount_sku_activity_ids",
    "super_reduce_early_activation_already_clear",
    "super_reduce_early_activation_withdrawn",
}


def _remark_key(segment: str) -> str | None:
    matched = re.match(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*=", segment)
    return matched.group(1).lower() if matched else None


def _runtime_remark_key(key: str | None) -> bool:
    return bool(
        key
        and (
            key in _RUNTIME_REMARK_KEYS
            or any(key.startswith(prefix) for prefix in _RUNTIME_REMARK_PREFIXES)
        )
    )


def _prepare_owned_remark(value: str | None) -> str:
    """Project the mixed legacy remark onto formal request-owned content."""
    return "; ".join(
        segment for segment in _remark_segments(value)
        if not _runtime_remark_key(_remark_key(segment))
    )


def _different_fields(plan: CampaignPlan, values: dict) -> list[str]:
    different = []
    for field in _IDENTITY_FIELDS:
        current = getattr(plan, field, None)
        desired = values.get(field)
        if field == "remark":
            current = _prepare_owned_remark(current)
            desired = _prepare_owned_remark(desired)
        if current != desired:
            different.append(field)
    return different


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


def _is_sign_record_enrichment(
        plan: CampaignPlan, values: dict, different: list[str]) -> bool:
    """Allow one-way addition of an exact read-only enrolled-record identity."""
    return bool(
        different == ["platform_sign_record_id"]
        and plan.status in ("draft", "precheck")
        and not str(getattr(plan, "platform_sign_record_id", None) or "").strip()
        and str(values.get("platform_sign_record_id") or "").isdigit()
        and str(getattr(plan, "platform_activity_mode", "")) == "fixed_window"
    )


def _normalized_item_ids(values) -> tuple[list[str], list[str]]:
    from app.services import no_sales_service

    raw = [str(value or "").strip() for value in (values or [])]
    normalized = no_sales_service.normalize_item_ids(raw)
    invalid = sorted(set(raw) - set(normalized))
    return sorted(set(normalized)), invalid


def _execution_receipts(db: Session, plan: CampaignPlan) -> tuple[list[dict], bool]:
    """Return bounded signup receipts and whether their storage was malformed."""
    from app.services import settings_service

    raw = settings_service.get(
        db, f"campaign_execution_receipts_{plan.id}", env_fallback=False)
    if not raw:
        return [], False
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return [], True
    if not isinstance(payload, list) or any(
            not isinstance(row, dict) for row in payload):
        return [], True
    return payload, False


def _unsubmitted_correction_guard(db: Session, plan: CampaignPlan) -> dict:
    """Fail closed unless this formal plan has no platform-write evidence."""
    if plan.status in ("draft", "precheck"):
        return {"ok": True, "status": plan.status, "alarmed": False}
    if plan.status != "alarmed":
        return {
            "ok": False, "status": plan.status,
            "reason": "status_not_unsubmitted",
        }

    receipts, malformed = _execution_receipts(db, plan)
    if malformed:
        return {
            "ok": False, "status": plan.status,
            "reason": "execution_receipts_malformed",
        }
    submitted_receipts = [
        row for row in receipts if bool(row.get("submitted"))
    ]
    if submitted_receipts:
        return {
            "ok": False, "status": plan.status,
            "reason": "signup_submission_receipt_present",
            "submitted_receipt_count": len(submitted_receipts),
        }
    recon_count = len(db.execute(select(CampaignReconReport).where(
        CampaignReconReport.plan_id == plan.id)).scalars().all())
    if recon_count:
        return {
            "ok": False, "status": plan.status,
            "reason": "reconciliation_evidence_present",
            "recon_report_count": recon_count,
        }
    write_markers = sorted({
        key for segment in _remark_segments(plan.remark)
        for key in [_remark_key(segment)]
        if key in _PLATFORM_WRITE_REMARK_KEYS
    })
    if write_markers:
        return {
            "ok": False, "status": plan.status,
            "reason": "platform_write_marker_present",
            "markers": write_markers,
        }
    return {
        "ok": True,
        "status": plan.status,
        "alarmed": True,
        "submitted_receipt_count": 0,
        "recon_report_count": 0,
        "platform_write_markers": [],
    }


def _derived_empty_active_scope(plan: CampaignPlan) -> bool:
    """Recognize the exact read-only-refresh drift seen on legacy plan 7."""
    segments = _remark_segments(plan.remark)
    active = [
        segment for segment in segments
        if (_remark_key(segment) or "").lower() == "official_active_items"
    ]
    if len(active) != 1 or not re.fullmatch(
            r"official_active_items\s*=\s*", active[0], flags=re.IGNORECASE):
        return False
    keys = {_remark_key(segment) for segment in segments}
    if "official_all_store" in keys or "official_exempt_items" in keys:
        return False
    return bool(keys & {
        "platform_qualified_items",
        "platform_no_sales_items",
        "platform_hard_failed_items",
        "current_activity_prices",
    })


def _set_all_store_exempt_scope(plan: CampaignPlan, desired: list[str]) -> None:
    """Replace only formal official-scope markers; preserve runtime evidence."""
    segments = [
        segment for segment in _remark_segments(plan.remark)
        if (_remark_key(segment) or "").lower() not in {
            "official_active_items", "official_all_store", "official_exempt_items",
        }
    ]
    segments.extend([
        "official_all_store=true",
        f"official_exempt_items={','.join(desired)}",
    ])
    plan.remark = "; ".join(segments)


def correct_official_exemptions(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_item_ids, desired_item_ids) -> dict:
    """CAS-update only the plan-scoped all-store official exemption marker."""
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.workflow_key == workflow_key)).scalar_one_or_none()
    if plan is None:
        return {"ok": False, "error": "workflow_not_found"}
    if plan.id != expected_plan_id:
        return {
            "ok": False, "error": "workflow_plan_mismatch",
            "plan_id": plan.id,
        }
    correction_guard = _unsubmitted_correction_guard(db, plan)
    if not correction_guard.get("ok"):
        return {
            "ok": False, "error": "unsubmitted_plan_required",
            "plan_id": plan.id, "status": plan.status,
            "submission_guard": correction_guard,
        }
    expected, invalid_expected = _normalized_item_ids(expected_item_ids)
    desired, invalid_desired = _normalized_item_ids(desired_item_ids)
    if invalid_expected or invalid_desired:
        return {
            "ok": False, "error": "invalid_item_ids",
            "invalid_item_ids": sorted(set(invalid_expected + invalid_desired)),
        }
    scope = campaign_service.official_scope_for_plan(plan)
    marker_matches = list(re.finditer(
        r"(?:^|[;\n；])\s*official_exempt_items\s*=\s*[^;\n；]*",
        str(plan.remark or ""), flags=re.IGNORECASE))
    drifted_empty_scope = _derived_empty_active_scope(plan)
    normal_all_store_scope = bool(
        scope.get("configured") and scope.get("all_store")
        and not scope.get("errors") and len(marker_matches) == 1
    )
    if not normal_all_store_scope and not drifted_empty_scope:
        return {
            "ok": False, "error": "official_scope_not_correctable",
            "scope_errors": scope.get("errors") or [],
        }
    current = [] if drifted_empty_scope else sorted(
        scope.get("exempt_items") or set())
    if current == desired and not drifted_empty_scope:
        return {
            "ok": True, "changed": False, "idempotent_replay": True,
            "workflow_key": workflow_key, "plan": plan,
            "previous_official_exempt_item_ids": current,
            "official_exempt_item_ids": desired,
            "repaired_derived_scope": False,
            "submission_guard": correction_guard,
            "execution_boundary": {
                "plan_scoped_only": True, "permanent_exclusion_write": False,
                "platform_write": False, "account_action": False,
                "notification": False, "automatic_retry": False,
            },
        }
    if current != expected:
        return {
            "ok": False, "error": "official_exemptions_compare_failed",
            "plan_id": plan.id,
            "expected_official_exempt_item_ids": expected,
            "current_official_exempt_item_ids": current,
        }
    _set_all_store_exempt_scope(plan, desired)
    db.commit()
    db.refresh(plan)
    return {
        "ok": True, "changed": True, "idempotent_replay": False,
        "workflow_key": workflow_key, "plan": plan,
        "previous_official_exempt_item_ids": current,
        "official_exempt_item_ids": desired,
        "repaired_derived_scope": drifted_empty_scope,
        "submission_guard": correction_guard,
        "execution_boundary": {
            "plan_scoped_only": True, "permanent_exclusion_write": False,
            "platform_write": False, "account_action": False,
            "notification": False, "automatic_retry": False,
        },
    }


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
        elif _is_sign_record_enrichment(plan, values, different):
            plan.platform_sign_record_id = values["platform_sign_record_id"]
            db.commit()
            db.refresh(plan)
            repaired_fields = ["platform_sign_record_id"]
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
                "platform_read": "current_activity_export_and_candidate_read_if_required",
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
        "candidate_floor_refresh": refreshed.get("candidate_floor_refresh"),
        "candidate_evidence": refreshed.get("candidate_evidence"),
        "placeholder_price_refresh": refreshed.get("placeholder_price_refresh"),
        "export_evidence": refreshed.get("export_evidence"),
        "gate_results": {
            "R16": by_rule.get("R16"),
            "R17": by_rule.get("R17"),
        },
    })
    platform_read = (
        "current_activity_export_and_candidate_selectable_items"
        if refreshed.get("candidate_evidence")
        else "current_activity_export_only"
    )
    package["execution_boundary"].update({
        "platform_read": platform_read,
        "platform_write": False,
        "account_action": False,
        "notification": False,
        "automatic_retry": False,
    })
    return package

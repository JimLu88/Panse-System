"""Durable campaign preparation compiler.

This module deliberately stops immediately before any platform write.  It
turns the current ERP truth plus read-only platform evidence into one immutable
bundle, isolates item-scoped defects, and records every excluded item.  It does
not upload, submit, edit prices, rotate SKU identities, notify, or retry.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import (
    CampaignEvidenceSnapshot,
    CampaignExecutionAttempt,
    CampaignPlan,
    CampaignPreparationBundle,
)
from app.services import (
    campaign_policy_service,
    campaign_service,
    campaign_workflow_service,
)


READY_STATE = "ready_for_final_submission"
BLOCKED_STATE = "blocked_before_submission"
MAX_BUNDLE_LIFETIME_HOURS = 6
COMPILER_SCHEMA_VERSION = "2026-09-03.2"


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_sha256(value) -> str:
    raw = json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _item_ids(value, *, allow_scalar: bool = True) -> set[str]:
    found: set[str] = set()
    if allow_scalar and isinstance(value, (str, int)):
        item_id = str(value).strip()
        if item_id.isdigit() and 4 <= len(item_id) <= 20:
            found.add(item_id)
    elif isinstance(value, dict):
        for key in ("taobao_item_id", "item_id"):
            item_id = str(value.get(key) or "").strip()
            if item_id.isdigit() and 4 <= len(item_id) <= 20:
                found.add(item_id)
        for nested in value.values():
            # A rule detail can contain many numeric SKU IDs, record IDs and
            # prices.  Only explicit item-id fields inside a mapping are item
            # identities; recursively treating every scalar as an item would
            # manufacture duplicate pseudo-products in the preparation bundle.
            found.update(_item_ids(nested, allow_scalar=False))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found.update(_item_ids(nested, allow_scalar=allow_scalar))
    return found


def _identity(plan: CampaignPlan) -> dict:
    identity = campaign_service._campaign_identity(plan)
    return _jsonable(identity)


def _inventory_snapshot(db: Session, plan: CampaignPlan) -> tuple[list[dict], dict[str, dict]]:
    listed_codes = campaign_service._erp_listed_product_codes(db)
    rows: list[dict] = []
    by_sku_id: dict[str, dict] = {}
    for sku, promo in campaign_service._mapped_pairs(db):
        if listed_codes is not None and str(sku.product_code or "") not in listed_codes:
            continue
        item_id = str(promo.taobao_item_id or "").strip()
        for sku_id in campaign_service._expand_sku_ids(promo):
            row = {
                "taobao_item_id": item_id,
                "taobao_sku_id": str(sku_id),
                "sku_code": str(sku.sku_code or ""),
                "product_code": str(sku.product_code or ""),
                "daily_price": (
                    str(sku.daily_price) if sku.daily_price is not None else None),
                "small_promo": (
                    str(sku.small_promo) if sku.small_promo is not None else None),
                "mid_promo": (
                    str(sku.mid_promo) if sku.mid_promo is not None else None),
                "big_promo": (
                    str(sku.big_promo) if sku.big_promo is not None else None),
                "is_custom_placeholder": bool(sku.is_custom_placeholder),
                "mapping_updated_at": _jsonable(getattr(promo, "updated_at", None)),
                "pricing_updated_at": _jsonable(getattr(sku, "updated_at", None)),
            }
            rows.append(row)
            by_sku_id[str(sku_id)] = row
    rows.sort(key=lambda row: (
        row["taobao_item_id"], row["taobao_sku_id"], row["sku_code"]))
    return rows, by_sku_id


def _latest_evidence(db: Session, plan: CampaignPlan) -> list[dict]:
    rows = db.execute(select(CampaignEvidenceSnapshot).where(
        CampaignEvidenceSnapshot.plan_id == plan.id,
        CampaignEvidenceSnapshot.workflow_key == plan.workflow_key,
    ).order_by(CampaignEvidenceSnapshot.created_at.desc()).limit(50)).scalars().all()
    return [{
        "id": row.id,
        "type": row.evidence_type,
        "request_id": row.request_id,
        "result_status": row.result_status,
        "scope_sha256": row.scope_sha256,
        "artifact_sha256": row.artifact_sha256,
        "created_at": _jsonable(row.created_at),
    } for row in rows]


def _attempt_guards(db: Session, plan: CampaignPlan) -> list[dict]:
    rows = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.plan_id == plan.id,
        CampaignExecutionAttempt.workflow_key == plan.workflow_key,
    ).order_by(CampaignExecutionAttempt.created_at)).scalars().all()
    return [{
        "attempt_id": row.id,
        "operation": row.operation,
        "state": row.state,
        "scope_sha256": row.scope_sha256,
        "write_claimed": bool(row.write_claimed),
        "platform_write_observed": row.platform_write_observed,
        "automatic_retry_allowed": bool(row.automatic_retry_allowed),
    } for row in rows]


def _add_reason(reasons: dict[str, list[dict]], item_id: str, reason: dict) -> None:
    if not item_id:
        return
    normalized = _jsonable(reason)
    if normalized not in reasons[item_id]:
        reasons[item_id].append(normalized)


def _stats_reasons(stats: dict) -> tuple[dict[str, list[dict]], set[str]]:
    reasons: dict[str, list[dict]] = defaultdict(list)
    excluded: set[str] = set()
    keyed = {
        "excluded_price_hold_items": "price_conflict",
        "incomplete_items": "sku_incomplete",
        "placeholder_missing_live_price": "placeholder_live_price_missing",
        "placeholder_price_blocked_items": "placeholder_price_guard",
        "custom_floor_guard_items": "custom_floor_guard",
    }
    for key, code in keyed.items():
        for row in stats.get(key) or []:
            for item_id in _item_ids(row):
                _add_reason(reasons, item_id, {
                    "code": code, "source": key, "detail": row})
    for item_id in stats.get("skipped_bad_price_items") or []:
        _add_reason(reasons, str(item_id), {
            "code": "erp_bad_price", "source": "skipped_bad_price_items"})
    for row in stats.get("excluded_whole_items") or []:
        for item_id in _item_ids(row):
            excluded.add(item_id)
            _add_reason(reasons, item_id, {
                "code": "whole_item_exclusion", "source": "erp_policy",
                "detail": row})
    for item_id in stats.get("excluded_official_exempt_items") or []:
        item_id = str(item_id)
        excluded.add(item_id)
        _add_reason(reasons, item_id, {
            "code": "plan_official_exemption", "source": "plan_scope"})
    return reasons, excluded


def _classify_checks(
        checks: list[dict], reasons: dict[str, list[dict]],
        *, candidate_items: set[str]) -> tuple[set[str], list[dict]]:
    blocked_items: set[str] = set()
    global_blockers: list[dict] = []
    for check in checks:
        level = str(check.get("level") or "")
        blocking_payload = check.get("items") or []
        if check.get("blocked_items"):
            blocking_payload = [*blocking_payload, *check["blocked_items"]]
        payload_ids = _item_ids(blocking_payload)
        ids = payload_ids & candidate_items
        is_blocking = level == "error" or bool(check.get("blocked_items"))
        if not is_blocking:
            continue
        if ids:
            blocked_items.update(ids)
            for item_id in ids:
                _add_reason(reasons, item_id, {
                    "code": "preflight_gate",
                    "rule": check.get("rule"),
                    "title": check.get("title"),
                    "level": level,
                })
        elif level == "error" and (not blocking_payload or not payload_ids):
            global_blockers.append({
                "code": "global_preflight_gate",
                "rule": check.get("rule"),
                "title": check.get("title"),
                "items": _jsonable(check.get("items") or []),
            })
    return blocked_items, global_blockers


def _verify_real_sku_prices(
        signup_rows: list[dict], inventory_by_sku: dict[str, dict],
) -> list[dict]:
    errors: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in signup_rows:
        key = (str(row.get("taobao_item_id") or ""),
               str(row.get("taobao_sku_id") or ""))
        if key in seen:
            errors.append({
                "code": "duplicate_signup_row",
                "taobao_item_id": key[0], "taobao_sku_id": key[1]})
            continue
        seen.add(key)
        inventory = inventory_by_sku.get(key[1])
        if not inventory:
            errors.append({
                "code": "signup_sku_not_in_erp_inventory",
                "taobao_item_id": key[0], "taobao_sku_id": key[1]})
            continue
        if bool(row.get("is_placeholder")):
            continue
        try:
            actual = Decimal(str(row.get("price"))).quantize(Decimal("0.01"))
            daily = Decimal(str(inventory.get("daily_price"))).quantize(
                Decimal("0.01"))
        except Exception:  # noqa: BLE001 - returned as a bounded gate result
            actual = daily = None
        if actual is None or daily is None or actual != daily:
            errors.append({
                "code": "real_sku_signup_price_not_erp_daily",
                "taobao_item_id": key[0], "taobao_sku_id": key[1],
                "signup_price": str(row.get("price")),
                "erp_daily_price": inventory.get("daily_price"),
            })
    return errors


def _serialize(bundle: CampaignPreparationBundle) -> dict:
    now = datetime.now(timezone.utc)
    expires_at = bundle.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    effective_state = bundle.state
    if effective_state == READY_STATE and expires_at <= now:
        effective_state = "expired_requires_readonly_refresh"
    return {
        "ok": True,
        "bundle_id": bundle.id,
        "plan_id": bundle.plan_id,
        "workflow_key": bundle.workflow_key,
        "revision": bundle.revision,
        "state": effective_state,
        "prepared_by": bundle.prepared_by,
        "ready_for_final_submission": effective_state == READY_STATE,
        "source_sha256": bundle.source_sha256,
        "policy_sha256": bundle.policy_sha256,
        "manifest_sha256": bundle.manifest_sha256,
        "identity": bundle.identity,
        "summary": bundle.summary,
        "signup_rows": bundle.signup_rows,
        "discount_rows": bundle.discount_rows,
        "item_decisions": bundle.item_decisions,
        "gate_results": bundle.gate_results,
        "evidence_snapshot_ids": bundle.evidence_snapshot_ids,
        "prepared_at": _jsonable(bundle.prepared_at),
        "expires_at": _jsonable(bundle.expires_at),
        "execution_boundary": bundle.execution_boundary,
    }


def compile_bundle(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str | None = None,
        refresh_evidence: bool = False,
        prepared_by: str = "system:campaign-preparation-compiler") -> dict:
    """Create/reuse the immutable preparation package for one exact plan."""
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.workflow_key == workflow_key)).scalar_one_or_none()
    if plan is None:
        return {"ok": False, "error": "workflow_not_found"}
    if int(plan.id) != int(expected_plan_id):
        return {"ok": False, "error": "workflow_plan_mismatch", "plan_id": plan.id}
    if expected_status is not None and plan.status != expected_status:
        return {
            "ok": False, "error": "plan_status_compare_failed",
            "expected_status": expected_status, "actual_status": plan.status,
        }
    if refresh_evidence:
        refreshed = campaign_workflow_service.refresh_evidence_and_prepare(
            db, workflow_key=workflow_key, expected_plan_id=expected_plan_id)
        if not refreshed.get("ok"):
            return {**refreshed, "bundle_created": False}

    policy = campaign_policy_service.require_policy()
    identity = _identity(plan)
    inventory, inventory_by_sku = _inventory_snapshot(db, plan)
    signup_rows, signup_stats = campaign_service.build_signup_rows(db, plan)
    discount_rows, discount_stats = campaign_service.build_discount_rows(db, plan)
    checks = campaign_service.preflight(db, plan)
    evidence = _latest_evidence(db, plan)
    attempts = _attempt_guards(db, plan)

    reasons, excluded_items = _stats_reasons(signup_stats)
    discount_reasons, discount_excluded = _stats_reasons(discount_stats)
    excluded_items |= discount_excluded
    for item_id, rows in discount_reasons.items():
        for row in rows:
            _add_reason(reasons, item_id, row)
    generated_items = {
        str(row.get("taobao_item_id") or "") for row in signup_rows
        if str(row.get("taobao_item_id") or "")
    }
    all_items = {
        str(row["taobao_item_id"]) for row in inventory
        if str(row.get("taobao_item_id") or "")
    }
    blocked_items, global_blockers = _classify_checks(
        checks, reasons, candidate_items=all_items)

    if not identity.get("ok"):
        global_blockers.append({
            "code": "campaign_identity_incomplete",
            "missing": identity.get("missing") or [],
        })

    invariant_errors = _verify_real_sku_prices(signup_rows, inventory_by_sku)
    for error in invariant_errors:
        item_id = str(error.get("taobao_item_id") or "")
        blocked_items.add(item_id)
        _add_reason(reasons, item_id, error)

    claimed_attempts = [
        row for row in attempts
        if row["write_claimed"] and row["state"] != "completed"
    ]
    if claimed_attempts:
        global_blockers.append({
            "code": "existing_claimed_attempt_requires_readback_or_scoped_recovery",
            "attempts": claimed_attempts,
        })

    safe_items = generated_items - blocked_items - excluded_items
    scoped_checks: list[dict] = []
    # Re-run gates for the shrinking safe subset. Item-scoped defects are
    # isolated; a true global defect still stops the package.
    for _ in range(len(safe_items) + 1):
        if not safe_items:
            break
        scoped_checks = campaign_service.preflight(
            db, plan, exact_item_scope=set(safe_items))
        newly_blocked, scoped_global = _classify_checks(
            scoped_checks, reasons, candidate_items=set(safe_items))
        for blocker in scoped_global:
            if blocker not in global_blockers:
                global_blockers.append(blocker)
        if not newly_blocked:
            break
        safe_items -= newly_blocked
        blocked_items |= newly_blocked

    safe_signup = [
        _jsonable(row) for row in signup_rows
        if str(row.get("taobao_item_id") or "") in safe_items
    ]
    safe_discount = [
        _jsonable(row) for row in discount_rows
        if str(row.get("taobao_item_id") or "") in safe_items
    ]
    manifest_sha = (_canonical_sha256({
        "identity": identity,
        "policy_sha256": str(policy.get("_sha256") or ""),
        "signup_rows": safe_signup,
        "discount_rows": safe_discount,
    }) if safe_signup else None)

    item_decisions = []
    prior_no_sales = set(signup_stats.get("advisory_prior_no_sales_items") or [])
    sku_counts: dict[str, int] = defaultdict(int)
    for row in inventory:
        sku_counts[str(row["taobao_item_id"])] += 1
    for item_id in sorted(all_items | set(reasons)):
        if item_id in safe_items:
            state = "ready"
        elif item_id in excluded_items:
            state = "excluded_by_explicit_policy"
        else:
            state = "deferred_whole_item"
            if not reasons.get(item_id):
                _add_reason(reasons, item_id, {
                    "code": "not_in_generated_signup_scope"})
        item_decisions.append({
            "taobao_item_id": item_id,
            "state": state,
            "mapped_sku_count": sku_counts.get(item_id, 0),
            "prior_no_sales_advisory": item_id in prior_no_sales,
            "reasons": reasons.get(item_id) or [],
        })

    decision_state_counts = {
        state: sum(row["state"] == state for row in item_decisions)
        for state in (
            "ready", "deferred_whole_item", "excluded_by_explicit_policy")
    }
    if sum(decision_state_counts.values()) != len(item_decisions):
        raise RuntimeError("campaign_preparation_decision_count_invariant_failed")

    ready = bool(safe_signup) and not global_blockers
    now = datetime.now(timezone.utc)
    lifetime = min(
        MAX_BUNDLE_LIFETIME_HOURS,
        campaign_policy_service.floor_evidence_max_age_hours())
    source_payload = {
        "compiler_schema_version": COMPILER_SCHEMA_VERSION,
        "identity": identity,
        "plan_status": plan.status,
        "policy_sha256": policy.get("_sha256"),
        "inventory": inventory,
        "signup_rows": safe_signup,
        "discount_rows": safe_discount,
        "item_decisions": item_decisions,
        "checks": _jsonable(scoped_checks or checks),
        "evidence": evidence,
        "attempts": attempts,
        "global_blockers": global_blockers,
    }
    source_sha = _canonical_sha256(source_payload)
    existing = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.workflow_key == workflow_key,
        CampaignPreparationBundle.source_sha256 == source_sha,
    )).scalar_one_or_none()
    if existing is not None:
        return {**_serialize(existing), "created": False, "reused": True}

    revision = int(db.execute(select(func.coalesce(
        func.max(CampaignPreparationBundle.revision), 0)).where(
            CampaignPreparationBundle.workflow_key == workflow_key
        )).scalar_one()) + 1
    state = READY_STATE if ready else BLOCKED_STATE
    bundle = CampaignPreparationBundle(
        id=secrets.token_hex(12),
        plan_id=plan.id,
        workflow_key=workflow_key,
        revision=revision,
        state=state,
        prepared_by=str(prepared_by or "system:unknown")[:128],
        source_sha256=source_sha,
        policy_sha256=str(policy.get("_sha256") or ""),
        manifest_sha256=manifest_sha,
        identity=identity,
        summary={
            "compiler_schema_version": COMPILER_SCHEMA_VERSION,
            "plan_status": plan.status,
            "total_item_count": len(item_decisions),
            "mapped_item_count": len(all_items),
            "ready_item_count": decision_state_counts["ready"],
            "ready_signup_row_count": len(safe_signup),
            "ready_discount_row_count": len(safe_discount),
            "deferred_item_count": decision_state_counts["deferred_whole_item"],
            "excluded_item_count": decision_state_counts[
                "excluded_by_explicit_policy"],
            "prior_no_sales_advisory_count": len(prior_no_sales & all_items),
            "global_blockers": global_blockers,
            "attempt_guards": attempts,
        },
        signup_rows=safe_signup,
        discount_rows=safe_discount,
        item_decisions=item_decisions,
        gate_results=_jsonable(scoped_checks or checks),
        evidence_snapshot_ids=[row["id"] for row in evidence],
        execution_boundary={
            "platform_read": bool(refresh_evidence),
            "platform_write": False,
            "account_action": False,
            "price_change": False,
            "sku_rotation": False,
            "notification": False,
            "automatic_retry": False,
            "write_claim_created": False,
            "allowed_next_step": (
                "campaign_program_final_once" if ready
                else "repair_or_refresh_then_compile_new_revision"),
        },
        prepared_at=now,
        expires_at=now + timedelta(hours=lifetime),
    )
    db.add(bundle)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(select(CampaignPreparationBundle).where(
            CampaignPreparationBundle.workflow_key == workflow_key,
            CampaignPreparationBundle.source_sha256 == source_sha,
        )).scalar_one()
        return {**_serialize(existing), "created": False, "reused": True}
    db.refresh(bundle)
    return {**_serialize(bundle), "created": True, "reused": False}


def get_latest_bundle(
        db: Session, *, workflow_key: str, expected_plan_id: int) -> dict:
    bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.workflow_key == workflow_key,
        CampaignPreparationBundle.plan_id == expected_plan_id,
    ).order_by(CampaignPreparationBundle.revision.desc())).scalar_one_or_none()
    if bundle is None:
        return {"ok": False, "error": "preparation_bundle_not_found"}
    return _serialize(bundle)

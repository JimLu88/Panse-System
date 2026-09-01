"""Evidence-led Taobao SKU identity ledger operations."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.sku_identity import SkuIdentity, SkuIdentityObservation, SkuPhysicalSlotProposal
from app.models.taobao_listing import TaobaoListing


def _clean(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _identity_payload(row: dict) -> dict:
    return {
        "taobao_item_id": str(row.get("taobao_item_id") or "").strip(),
        "taobao_sku_id": str(row.get("taobao_sku_id") or "").strip(),
        "merchant_code": _clean(row.get("merchant_code")),
        "sku_spec": _clean(row.get("sku_spec")),
        "sku_code": _clean(row.get("sku_code")),
        "product_code": _clean(row.get("product_code")),
        "is_custom_placeholder": bool(row.get("is_custom_placeholder")),
    }


def observe(db: Session, rows: list[dict], *, evidence_source: str,
            evidence_sha256: str, observed_at: datetime | None = None) -> dict:
    """Append observations and refuse to overwrite an established meaning."""
    if len(str(evidence_sha256 or "")) != 64:
        raise ValueError("sku_identity_evidence_sha256_required")
    now = observed_at or datetime.now(timezone.utc)
    inserted = refreshed = conflicts = skipped = 0
    for raw in rows:
        meaning = _identity_payload(raw)
        item_id, sku_id = meaning["taobao_item_id"], meaning["taobao_sku_id"]
        if not item_id.isdigit() or not sku_id.isdigit():
            skipped += 1
            continue
        digest = _hash(meaning)
        current = db.execute(select(SkuIdentity).where(
            SkuIdentity.taobao_item_id == item_id,
            SkuIdentity.taobao_sku_id == sku_id,
        ).with_for_update()).scalar_one_or_none()
        disposition = "created"
        if current is None:
            current = SkuIdentity(
                **meaning, identity_sha256=digest,
                first_observed_at=now, last_observed_at=now,
                latest_sale_state=_clean(raw.get("sale_state")),
                latest_daily_price=raw.get("daily_price"),
                latest_evidence_source=evidence_source,
                latest_evidence_sha256=evidence_sha256,
                conflict_detected=False,
            )
            db.add(current)
            db.flush()
            inserted += 1
        elif (current.identity_sha256 != digest
              and current.latest_evidence_source.startswith("erp_database_backfill")
              and meaning["merchant_code"] == meaning["sku_code"]
              and all(getattr(current, field) == meaning[field] for field in (
                  "taobao_item_id", "taobao_sku_id", "sku_spec", "sku_code",
                  "product_code", "is_custom_placeholder"))):
            # 0147's first production backfill exposed a legacy import quirk:
            # TaobaoListing.merchant_code may hold the product code while its
            # matched sku_code is the exact per-SKU merchant code. Preserve the
            # old observation, but correct the canonical backfill projection
            # before any platform evidence relies on it.
            current.merchant_code = meaning["merchant_code"]
            current.identity_sha256 = digest
            current.last_observed_at = now
            current.latest_sale_state = _clean(raw.get("sale_state"))
            current.latest_daily_price = raw.get("daily_price")
            current.latest_evidence_source = evidence_source
            current.latest_evidence_sha256 = evidence_sha256
            current.conflict_detected = False
            refreshed += 1
            disposition = "backfill_code_corrected"
        elif current.identity_sha256 != digest:
            current.conflict_detected = True
            conflicts += 1
            disposition = "identity_conflict"
        else:
            current.last_observed_at = now
            current.latest_sale_state = _clean(raw.get("sale_state"))
            current.latest_daily_price = raw.get("daily_price")
            current.latest_evidence_source = evidence_source
            current.latest_evidence_sha256 = evidence_sha256
            refreshed += 1
            disposition = "observed_same_identity"
        db.add(SkuIdentityObservation(
            identity_id=current.id,
            **meaning,
            daily_price=raw.get("daily_price"),
            sale_state=_clean(raw.get("sale_state")),
            observed_at=now,
            evidence_source=evidence_source,
            evidence_sha256=evidence_sha256,
            identity_sha256=digest,
            disposition=disposition,
            detail={"canonical_identity_sha256": current.identity_sha256},
        ))
    db.flush()
    return {"created": inserted, "refreshed": refreshed, "conflicts": conflicts, "skipped": skipped}


def backfill_from_erp(db: Session) -> dict:
    """Database-only backfill. Unknown historical data stays unknown."""
    sku_by_code = {row.sku_code: row for row in db.execute(select(PricingSku)).scalars()}
    listings = db.execute(select(TaobaoListing).order_by(TaobaoListing.id)).scalars().all()
    rows: list[dict] = []
    for listing in listings:
        sku = sku_by_code.get(str(listing.sku_code or listing.merchant_code or ""))
        rows.append({
            "taobao_item_id": listing.taobao_item_id,
            "taobao_sku_id": listing.taobao_sku_id,
            "merchant_code": listing.sku_code or listing.merchant_code,
            "sku_spec": listing.sku_spec,
            "sku_code": listing.sku_code or (sku.sku_code if sku else None),
            "product_code": listing.product_code or (sku.product_code if sku else None),
            "is_custom_placeholder": bool(sku.is_custom_placeholder) if sku else False,
            "daily_price": sku.daily_price if sku else listing.sku_price,
            "sale_state": "erp_listing_current",
        })
    listed_pairs = {(str(r["taobao_item_id"] or ""), str(r["taobao_sku_id"] or "")) for r in rows}
    for promo in db.execute(select(PricingSkuPromo)).scalars():
        sku = sku_by_code.get(promo.sku_code)
        for sku_id in [promo.taobao_sku_id, *(promo.alt_taobao_sku_ids or [])]:
            pair = (str(promo.taobao_item_id or ""), str(sku_id or ""))
            if pair in listed_pairs:
                continue
            rows.append({
                "taobao_item_id": pair[0], "taobao_sku_id": pair[1],
                "merchant_code": promo.sku_code, "sku_spec": sku.sku if sku else None,
                "sku_code": promo.sku_code, "product_code": sku.product_code if sku else None,
                "is_custom_placeholder": bool(sku.is_custom_placeholder) if sku else False,
                "daily_price": sku.daily_price if sku else None,
                "sale_state": "erp_mapping_current",
            })
    evidence_sha256 = _hash({"source": "erp_database_backfill", "rows": rows})
    result = observe(db, rows, evidence_source="erp_database_backfill:0147",
                     evidence_sha256=evidence_sha256)
    result.update({"source_rows": len(rows), "evidence_sha256": evidence_sha256,
                   "platform_read": False, "platform_write": False})
    return result


LIFT_DESK_PROPOSAL = {
    "taobao_item_id": "793202812082",
    "parent_merchant_code": "PPS2441004051311",
    "target_merchant_code": "PPS2441004051311B1",
    "source_option": "130cm 带高台",
    "target_option": "130cm 带高台升降桌",
    "slot_number": 1,
}


def ensure_lift_desk_proposal(db: Session, *, authorization_ref: str) -> dict:
    existing = db.execute(select(SkuPhysicalSlotProposal).where(
        SkuPhysicalSlotProposal.target_merchant_code == LIFT_DESK_PROPOSAL["target_merchant_code"]
    )).scalar_one_or_none()
    payload = {
        **LIFT_DESK_PROPOSAL,
        "parent_taobao_sku_id": None,
        "reason": "campaign_price_conflict_clean_physical_slot_pilot",
        "authorization_ref": authorization_ref,
        "lifecycle_state": "proposed",
        "product_create_status": "not_started",
        "product_save_status": "not_saved",
        "campaign_signup_status": "not_submitted",
        "proposed_fields": {
            "copy_policy": "source_page_exact_all_copyable_fields",
            "allowed_differences": ["option_name", "merchant_code"],
            "source_erp_prices": {"list_price": "9100.00", "daily_price": "6825.00",
                                  "small_promo": "4190.00", "mid_promo": "4050.00",
                                  "big_promo": "3830.00"},
        },
        "evidence_source": "user_authorized_proposal:2026-08-31",
    }
    payload["evidence_sha256"] = _hash(payload)
    if existing:
        if any(getattr(existing, key) != value for key, value in LIFT_DESK_PROPOSAL.items()):
            raise ValueError("lift_desk_slot_proposal_identity_conflict")
        return {"created": False, "id": existing.id, "state": existing.lifecycle_state}
    row = SkuPhysicalSlotProposal(**payload)
    db.add(row)
    db.flush()
    return {"created": True, "id": row.id, "state": row.lifecycle_state,
            "platform_write": False}


def mark_lift_desk_staged_unsaved(db: Session, *, result: dict) -> dict:
    """Record a successful transient preview; never mark it live or saved."""
    row = db.execute(select(SkuPhysicalSlotProposal).where(
        SkuPhysicalSlotProposal.target_merchant_code == LIFT_DESK_PROPOSAL["target_merchant_code"]
    ).with_for_update()).scalar_one()
    if (not result.get("ok") or result.get("platform_product_write") is not False
            or not result.get("requires_user_visual_check")
            or result.get("stopped_before") != "提交宝贝信息"):
        raise ValueError("lift_desk_unsaved_stage_receipt_invalid")
    row.lifecycle_state = "staged_unsaved"
    row.product_create_status = "previewed_unsaved"
    row.product_save_status = "not_saved"
    row.campaign_signup_status = "not_submitted"
    row.evidence_source = "web_agent_unsaved_calibration"
    row.evidence_sha256 = _hash({
        "manifest": result.get("manifest"),
        "field_copy": result.get("field_copy"),
        "screenshot": result.get("screenshot"),
        "platform_product_write": False,
    })
    db.flush()
    return {"id": row.id, "state": row.lifecycle_state,
            "product_save_status": row.product_save_status,
            "campaign_signup_status": row.campaign_signup_status,
            "evidence_sha256": row.evidence_sha256}


def mark_lift_desk_stage_failed(db: Session, *, result: dict) -> dict:
    """Record a failed unsaved preview without claiming product creation."""
    row = db.execute(select(SkuPhysicalSlotProposal).where(
        SkuPhysicalSlotProposal.target_merchant_code == LIFT_DESK_PROPOSAL["target_merchant_code"]
    ).with_for_update()).scalar_one()
    if (result.get("ok") is not False
            or result.get("platform_product_write") is not False):
        raise ValueError("lift_desk_failed_stage_receipt_invalid")
    failure = {
        "error": _clean(result.get("error")) or "unknown_unsaved_stage_failure",
        "job_id": _clean(result.get("job_id")),
        "source_count": result.get("source_count") or (result.get("field_copy") or {}).get("source_count"),
        "target_count": result.get("target_count") or (result.get("field_copy") or {}).get("target_count"),
        "diff": (result.get("field_copy") or {}).get("diff"),
        "price_stock": (result.get("field_copy") or {}).get("price_stock"),
        "default_2000_eliminated": (result.get("field_copy") or {}).get(
            "default_2000_eliminated"),
        "platform_product_write": False,
        "product_save_status": "not_saved",
        "campaign_signup_status": "not_submitted",
    }
    proposed = dict(row.proposed_fields or {})
    history = list(proposed.get("stage_failures") or [])[-9:]
    history.append({**failure, "receipt_sha256": _hash(failure)})
    proposed["stage_failures"] = history
    row.proposed_fields = proposed
    row.lifecycle_state = "staging_failed"
    row.product_create_status = "preview_failed"
    row.product_save_status = "not_saved"
    row.campaign_signup_status = "not_submitted"
    row.evidence_source = "web_agent_unsaved_calibration_failed"
    row.evidence_sha256 = _hash({
        "proposal_id": row.id, "latest_failure": failure,
        "platform_product_write": False})
    db.flush()
    return {"id": row.id, "state": row.lifecycle_state,
            "product_create_status": row.product_create_status,
            "product_save_status": row.product_save_status,
            "campaign_signup_status": row.campaign_signup_status,
            "failure": failure, "evidence_sha256": row.evidence_sha256}


def mark_lift_desk_draft_save_result(
        db: Session, *, result: dict,
        required_recovery_identity: dict | None = None) -> dict:
    """Persist verified draft-save, pre-write failure, or no-retry unknown state."""
    row = db.execute(select(SkuPhysicalSlotProposal).where(
        SkuPhysicalSlotProposal.target_merchant_code == LIFT_DESK_PROPOSAL[
            "target_merchant_code"]
    ).with_for_update()).scalar_one()
    readback = dict(result.get("readback") or {})
    recovery = dict(result.get("draft_recovery") or {})
    recovery_exact = (required_recovery_identity is None or all(
        recovery.get(key) == value
        for key, value in required_recovery_identity.items()))
    verified = bool(
        result.get("ok") and result.get("draft_saved")
        and result.get("listed") is False
        and result.get("campaign_status") == "not_submitted"
        and readback.get("ok")
        and readback.get("item_id") == LIFT_DESK_PROPOSAL["taobao_item_id"]
        and readback.get("target_merchant_code")
        == LIFT_DESK_PROPOSAL["target_merchant_code"]
        and readback.get("option_count") == 13
        and readback.get("sku_row_count") == 13
        and readback.get("diff") == []
        and readback.get("rendered_missing") == []
        and readback.get("input_value_missing") == []
        and readback.get("invalid_rows") == []
        and readback.get("preexisting_sku_diff") == []
        and recovery_exact)
    platform_write = result.get("platform_product_write") is True
    no_retry = result.get("automatic_retry_allowed") is False
    if verified:
        row.lifecycle_state = "saved_draft_verified"
        row.product_create_status = "created_in_platform_draft"
        row.product_save_status = "saved_draft_verified"
        row.campaign_signup_status = "not_submitted"
        row.evidence_source = "web_agent_platform_draft_readback"
    elif platform_write or no_retry:
        row.lifecycle_state = "draft_save_result_unknown"
        row.product_create_status = "possible_platform_draft"
        row.product_save_status = "result_unknown"
        row.campaign_signup_status = "not_submitted"
        row.evidence_source = "web_agent_platform_draft_save_unknown"
    else:
        row.lifecycle_state = "draft_save_prewrite_failed"
        row.product_create_status = "not_created"
        row.product_save_status = "not_saved"
        row.campaign_signup_status = "not_submitted"
        row.evidence_source = "web_agent_platform_draft_prewrite_failed"
    proposed = dict(row.proposed_fields or {})
    proposed["draft_save"] = {
        "verified": verified,
        "already_saved": bool(result.get("already_saved")),
        "platform_product_write": platform_write,
        "automatic_retry_allowed": False if (verified or platform_write or no_retry) else True,
        "error": _clean(result.get("error")),
        "job_id": _clean(result.get("job_id")),
        "readback": readback,
        "draft_recovery": recovery,
        "required_recovery_identity": required_recovery_identity,
        "claim_path": _clean(result.get("claim_path")),
    }
    row.proposed_fields = proposed
    row.evidence_sha256 = _hash({
        "proposal_id": row.id,
        "state": row.lifecycle_state,
        "draft_save": proposed["draft_save"],
        "campaign_signup_status": "not_submitted",
    })
    db.flush()
    return {
        "id": row.id, "state": row.lifecycle_state,
        "product_create_status": row.product_create_status,
        "product_save_status": row.product_save_status,
        "campaign_signup_status": row.campaign_signup_status,
        "automatic_retry_allowed": proposed["draft_save"]["automatic_retry_allowed"],
        "evidence_sha256": row.evidence_sha256,
    }


def mark_lift_desk_draft_readback_result(db: Session, *, result: dict) -> dict:
    """Close an unknown save result using a later, strictly read-only proof."""
    row = db.execute(select(SkuPhysicalSlotProposal).where(
        SkuPhysicalSlotProposal.target_merchant_code == LIFT_DESK_PROPOSAL[
            "target_merchant_code"]
    ).with_for_update()).scalar_one()
    readback = dict(result.get("readback") or {})
    verified = bool(
        result.get("ok") and result.get("draft_saved")
        and result.get("listed") is False
        and result.get("campaign_status") == "not_submitted"
        and result.get("read_only") is True
        and result.get("platform_product_write") is False
        and readback.get("ok")
        and readback.get("item_id") == LIFT_DESK_PROPOSAL["taobao_item_id"]
        and readback.get("target_merchant_code")
        == LIFT_DESK_PROPOSAL["target_merchant_code"]
        and readback.get("option_count") == 13
        and readback.get("sku_row_count") == 13
        and readback.get("diff") == []
        and readback.get("rendered_missing") == []
        and readback.get("input_value_missing") == []
        and readback.get("invalid_rows") == []
        and readback.get("preexisting_sku_diff") == []
    )
    if verified:
        row.lifecycle_state = "saved_draft_verified"
        row.product_create_status = "created_in_platform_draft"
        row.product_save_status = "saved_draft_verified"
        row.campaign_signup_status = "not_submitted"
        row.evidence_source = "web_agent_platform_draft_readback"
    proposed = dict(row.proposed_fields or {})
    proposed["draft_readback"] = {
        "verified": verified,
        "read_only": result.get("read_only") is True,
        "platform_product_write": result.get("platform_product_write") is True,
        "submission_action": result.get("submission_action") is True,
        "error": _clean(result.get("error")),
        "job_id": _clean(result.get("job_id")),
        "readback": readback,
    }
    row.proposed_fields = proposed
    row.evidence_sha256 = _hash({
        "proposal_id": row.id,
        "state": row.lifecycle_state,
        "draft_save": proposed.get("draft_save"),
        "draft_readback": proposed["draft_readback"],
        "campaign_signup_status": row.campaign_signup_status,
    })
    db.flush()
    return {
        "id": row.id, "verified": verified, "state": row.lifecycle_state,
        "product_create_status": row.product_create_status,
        "product_save_status": row.product_save_status,
        "campaign_signup_status": row.campaign_signup_status,
        "automatic_retry_allowed": False,
        "evidence_sha256": row.evidence_sha256,
    }


def query(db: Session, *, item_id: str | None = None, merchant_code: str | None = None) -> dict:
    stmt = select(SkuIdentity).order_by(SkuIdentity.taobao_item_id, SkuIdentity.taobao_sku_id)
    if item_id:
        stmt = stmt.where(SkuIdentity.taobao_item_id == str(item_id))
    if merchant_code:
        stmt = stmt.where(SkuIdentity.merchant_code == str(merchant_code))
    rows = db.execute(stmt).scalars().all()
    proposals_stmt = select(SkuPhysicalSlotProposal).order_by(SkuPhysicalSlotProposal.id)
    if item_id:
        proposals_stmt = proposals_stmt.where(SkuPhysicalSlotProposal.taobao_item_id == str(item_id))
    if merchant_code:
        proposals_stmt = proposals_stmt.where(or_(
            SkuPhysicalSlotProposal.parent_merchant_code == str(merchant_code),
            SkuPhysicalSlotProposal.target_merchant_code == str(merchant_code)))
    proposals = db.execute(proposals_stmt).scalars().all()
    return {
        "items": [{
            "item_id": r.taobao_item_id, "taobao_sku_id": r.taobao_sku_id,
            "merchant_code": r.merchant_code, "sku_spec": r.sku_spec,
            "sku_code": r.sku_code, "product_code": r.product_code,
            "placeholder": r.is_custom_placeholder,
            "daily_price": str(r.latest_daily_price) if r.latest_daily_price is not None else None,
            "sale_state": r.latest_sale_state,
            "first_observed_at": r.first_observed_at.isoformat(),
            "last_observed_at": r.last_observed_at.isoformat(),
            "evidence_source": r.latest_evidence_source,
            "evidence_sha256": r.latest_evidence_sha256,
            "identity_conflict": r.conflict_detected,
        } for r in rows],
        "proposals": [{
            "id": r.id, "item_id": r.taobao_item_id,
            "parent_merchant_code": r.parent_merchant_code,
            "target_merchant_code": r.target_merchant_code,
            "source_option": r.source_option, "target_option": r.target_option,
            "slot_number": r.slot_number, "state": r.lifecycle_state,
            "product_save_status": r.product_save_status,
            "campaign_signup_status": r.campaign_signup_status,
            "proposed_fields": r.proposed_fields,
        } for r in proposals],
        "read_only": True,
    }


def assert_exact_platform_snapshot(db: Session, rows: list[dict], *, item_ids: set[str]) -> dict:
    """Compare an official export with the canonical ledger and fail closed on drift."""
    observed = {(str(r.get("item_id") or r.get("taobao_item_id") or ""),
                 str(r.get("sku_id") or r.get("taobao_sku_id") or ""))
                for r in rows}
    observed = {pair for pair in observed if pair[0] in item_ids and pair[1].isdigit()}
    ledger = set(db.execute(select(SkuIdentity.taobao_item_id, SkuIdentity.taobao_sku_id).where(
        SkuIdentity.taobao_item_id.in_(sorted(item_ids)),
        SkuIdentity.conflict_detected.is_(False),
    )).all())
    conflicts = db.execute(select(SkuIdentity).where(
        SkuIdentity.taobao_item_id.in_(sorted(item_ids)),
        SkuIdentity.conflict_detected.is_(True),
    )).scalars().all()
    return {
        "ok": observed == ledger and not conflicts,
        "missing_in_ledger": sorted(observed - ledger),
        "missing_on_platform": sorted(ledger - observed),
        "conflicts": [{"item_id": r.taobao_item_id, "sku_id": r.taobao_sku_id} for r in conflicts],
    }


def campaign_manifest_gate(db: Session, rows: list[dict]) -> dict:
    """DB-only preparation gate; execution still performs official full export."""
    expected = {(str(r.get("taobao_item_id") or ""), str(r.get("taobao_sku_id") or ""))
                for r in rows}
    expected = {pair for pair in expected if pair[0].isdigit() and pair[1].isdigit()}
    if not expected:
        return {"ok": True, "level": "pass", "missing": [], "conflicts": [],
                "unverified": []}
    item_ids = sorted({pair[0] for pair in expected})
    identities = db.execute(select(SkuIdentity).where(
        SkuIdentity.taobao_item_id.in_(item_ids))).scalars().all()
    total_ledger_rows = db.execute(select(SkuIdentity.id).limit(1)).first()
    if total_ledger_rows is None:
        # Fresh test/install before the explicit 0147 DB-only backfill.  This is
        # visible and non-passing, but does not pretend drift was observed.
        return {"ok": True, "level": "warn", "missing": sorted(expected),
                "conflicts": [], "unverified": sorted(expected)}
    by_pair = {(r.taobao_item_id, r.taobao_sku_id): r for r in identities}
    missing = sorted(expected - set(by_pair))
    conflicts = sorted(pair for pair in expected if pair in by_pair and by_pair[pair].conflict_detected)
    unverified = sorted(pair for pair in expected if pair in by_pair
                        and by_pair[pair].latest_evidence_source.startswith("erp_database_backfill"))
    return {"ok": not missing and not conflicts,
            "level": "error" if missing or conflicts else "warn" if unverified else "pass",
            "missing": missing, "conflicts": conflicts, "unverified": unverified}

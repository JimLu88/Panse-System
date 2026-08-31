"""Controlled, append-only physical SKU slot pools for campaign eligibility."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignSkuSlot, CampaignSkuSlotAttempt


STATES = {"clean", "reserved", "active", "cooling"}


def custom_floor(baseline_daily_price) -> Decimal:
    return (Decimal(str(baseline_daily_price)) * Decimal("0.20")).quantize(
        Decimal("0.01"), ROUND_HALF_UP)


def attribute_sha256(attributes: dict) -> str:
    raw = json.dumps(attributes, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def choose_clean_slot(db: Session, *, sku_code: str, item_id: str,
                      now: datetime | None = None) -> CampaignSkuSlot | None:
    del now  # Cooling is evidence-driven; elapsed time alone never makes a slot clean.
    rows = db.execute(select(CampaignSkuSlot).where(
        CampaignSkuSlot.sku_code == sku_code,
        CampaignSkuSlot.taobao_item_id == item_id,
    ).order_by(CampaignSkuSlot.id)).scalars().all()
    for row in rows:
        if row.state == "clean":
            return row
    return None


def reserve(db: Session, slot: CampaignSkuSlot, *, workflow_key: str) -> None:
    row = db.execute(select(CampaignSkuSlot).where(
        CampaignSkuSlot.id == slot.id).with_for_update()).scalar_one()
    if row.state != "clean":
        raise ValueError("campaign_sku_slot_not_clean")
    active = db.execute(select(CampaignSkuSlot).where(
        CampaignSkuSlot.sku_code == row.sku_code,
        CampaignSkuSlot.state.in_(("reserved", "active")),
        CampaignSkuSlot.id != row.id,
    )).scalars().all()
    if any(x.state == "reserved" for x in active):
        raise ValueError("campaign_sku_slot_already_reserved")
    row.state = "reserved"
    row.last_workflow_key = workflow_key
    db.flush()


def release_cooling_slot(db: Session, slot_id: int, *, evidence: dict) -> None:
    """Release only with fresh, exact platform evidence that the slot is clean."""
    row = db.execute(select(CampaignSkuSlot).where(
        CampaignSkuSlot.id == slot_id).with_for_update()).scalar_one()
    required = {
        "taobao_item_id": row.taobao_item_id,
        "taobao_sku_id": row.taobao_sku_id,
        "history_clear": True,
    }
    if row.state != "cooling" or any(evidence.get(k) != v for k, v in required.items()):
        raise ValueError("campaign_sku_slot_clean_evidence_required")
    observed_at = evidence.get("observed_at")
    artifact_sha256 = str(evidence.get("artifact_sha256") or "")
    if not observed_at or len(artifact_sha256) != 64:
        raise ValueError("campaign_sku_slot_clean_evidence_required")
    row.state = "clean"
    row.cooling_until = None
    row.floor_evidence = dict(evidence)
    db.flush()


def ensure_attempt(db: Session, *, workflow_key: str, item_id: str,
                   sku_code: str, manifest: dict,
                   source_slot_id: int | None = None,
                   target_slot_id: int | None = None) -> CampaignSkuSlotAttempt:
    digest = attribute_sha256(manifest)
    existing = db.execute(select(CampaignSkuSlotAttempt).where(
        CampaignSkuSlotAttempt.workflow_key == workflow_key,
        CampaignSkuSlotAttempt.sku_code == sku_code,
    )).scalar_one_or_none()
    if existing:
        if (existing.manifest_sha256 != digest
                or existing.taobao_item_id != item_id):
            raise ValueError("campaign_sku_slot_attempt_scope_conflict")
        return existing
    row = CampaignSkuSlotAttempt(
        id=secrets.token_hex(12), workflow_key=workflow_key,
        taobao_item_id=item_id, sku_code=sku_code,
        source_slot_id=source_slot_id, target_slot_id=target_slot_id,
        manifest_sha256=digest,
        state="prepared", write_claimed=False,
        result_summary={"execution_boundary": {"platform_write": False}},
    )
    db.add(row)
    db.flush()
    return row


def claim_write(db: Session, attempt_id: str, *, request_id: str) -> None:
    row = db.execute(select(CampaignSkuSlotAttempt).where(
        CampaignSkuSlotAttempt.id == attempt_id).with_for_update()).scalar_one()
    if row.write_claimed or row.state != "prepared":
        raise ValueError("campaign_sku_slot_attempt_already_claimed")
    row.write_claimed = True
    row.state = "write_claimed"
    row.request_id = request_id
    db.flush()


def finalize_switch(db: Session, attempt_id: str, *, platform_write: bool,
                    success: bool, result: dict) -> None:
    """Atomically settle one claimed mutation; unknown writes never re-enter clean."""
    attempt = db.execute(select(CampaignSkuSlotAttempt).where(
        CampaignSkuSlotAttempt.id == attempt_id).with_for_update()).scalar_one()
    if not attempt.write_claimed or attempt.state != "write_claimed":
        raise ValueError("campaign_sku_slot_attempt_not_claimed")
    source = (db.execute(select(CampaignSkuSlot).where(
        CampaignSkuSlot.id == attempt.source_slot_id).with_for_update()).scalar_one()
        if attempt.source_slot_id else None)
    target = (db.execute(select(CampaignSkuSlot).where(
        CampaignSkuSlot.id == attempt.target_slot_id).with_for_update()).scalar_one()
        if attempt.target_slot_id else None)
    if success:
        if source is None or target is None or target.state != "reserved":
            raise ValueError("campaign_sku_slot_transition_invalid")
        source.state = "cooling"
        source.active_until = datetime.now(timezone.utc)
        target.state = "active"
        target.active_from = datetime.now(timezone.utc)
        attempt.state = "succeeded"
    else:
        attempt.state = "failed"
        if target is not None and target.state == "reserved":
            target.state = "cooling" if platform_write else "clean"
    attempt.result_summary = {
        **dict(result or {}),
        "execution_boundary": {"platform_write": bool(platform_write)},
    }
    db.flush()


def seed_active_slots(db: Session) -> dict:
    """Snapshot current mappings once; never rewrite the original price baseline."""
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo

    promos = {p.sku_code: p for p in db.execute(
        select(PricingSkuPromo)).scalars().all()}
    created = 0
    existing = 0
    for sku in db.execute(select(PricingSku).order_by(PricingSku.sku_code)).scalars():
        promo = promos.get(sku.sku_code)
        item_id = str(getattr(promo, "taobao_item_id", "") or "").strip()
        if not item_id:
            continue
        ids = []
        for raw in [promo.taobao_sku_id, *(promo.alt_taobao_sku_ids or [])]:
            sid = str(raw or "").strip()
            if sid and sid not in ids:
                ids.append(sid)
        attrs = {
            "product_code": sku.product_code,
            "sku": sku.sku or "",
            "size_info": sku.size_info or "",
        }
        for index, sid in enumerate(ids):
            row = db.execute(select(CampaignSkuSlot).where(
                CampaignSkuSlot.taobao_sku_id == sid)).scalar_one_or_none()
            if row:
                existing += 1
                continue
            baseline = Decimal(str(sku.daily_price)) if sku.daily_price is not None else None
            db.add(CampaignSkuSlot(
                sku_code=sku.sku_code,
                taobao_item_id=item_id,
                taobao_sku_id=sid,
                physical_slot_code=f"{sku.sku_code}-LEGACY-{index + 1}-{sid}",
                state="active",
                attribute_sha256=attribute_sha256(attrs),
                baseline_daily_price=baseline,
                custom_min_final_price=(
                    custom_floor(baseline)
                    if baseline is not None and bool(sku.is_custom_placeholder)
                    else None
                ),
                active_from=datetime.now(timezone.utc),
            ))
            created += 1
    db.flush()
    return {"created": created, "existing": existing}


def immutable_baseline(db: Session, *, sku_code: str, item_id: str,
                       taobao_sku_id: str) -> Decimal | None:
    row = db.execute(select(CampaignSkuSlot).where(
        CampaignSkuSlot.sku_code == sku_code,
        CampaignSkuSlot.taobao_item_id == item_id,
        CampaignSkuSlot.taobao_sku_id == taobao_sku_id,
    )).scalar_one_or_none()
    return Decimal(str(row.baseline_daily_price)) if row and row.baseline_daily_price is not None else None

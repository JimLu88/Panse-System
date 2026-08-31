from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.campaign import CampaignSkuSlot
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_sku_slot_service as service


def _slot(db, sid, state="clean", cooling_until=None):
    row = CampaignSkuSlot(
        sku_code="PPS2441004051311", taobao_item_id="793202812082",
        taobao_sku_id=sid, physical_slot_code=f"PPS2441004051311-S{sid}",
        state=state, attribute_sha256="a" * 64,
        baseline_daily_price=Decimal("6825.00"), cooling_until=cooling_until)
    db.add(row)
    db.commit()
    return row


def test_custom_floor_is_twenty_percent():
    assert service.custom_floor("2000") == Decimal("400.00")


def test_elapsed_cooling_deadline_never_makes_slot_clean(db_session):
    now = datetime.now(timezone.utc)
    _slot(db_session, "9001", "cooling", now + timedelta(days=1))
    old = _slot(db_session, "9002", "cooling", now - timedelta(days=30))
    assert service.choose_clean_slot(
        db_session, sku_code=old.sku_code, item_id=old.taobao_item_id,
        now=now) is None


def test_cooling_slot_requires_exact_platform_evidence(db_session):
    row = _slot(db_session, "9003", "cooling")
    with pytest.raises(ValueError, match="clean_evidence_required"):
        service.release_cooling_slot(db_session, row.id, evidence={
            "taobao_item_id": row.taobao_item_id,
            "taobao_sku_id": row.taobao_sku_id,
            "history_clear": True,
        })
    service.release_cooling_slot(db_session, row.id, evidence={
        "taobao_item_id": row.taobao_item_id,
        "taobao_sku_id": row.taobao_sku_id,
        "history_clear": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "artifact_sha256": "f" * 64,
    })
    assert row.state == "clean"


def test_only_one_reservation_per_logical_sku(db_session):
    first = _slot(db_session, "9011")
    second = _slot(db_session, "9012")
    service.reserve(db_session, first, workflow_key="campaign:test")
    with pytest.raises(ValueError, match="already_reserved"):
        service.reserve(db_session, second, workflow_key="campaign:test")


def test_slot_mutation_attempt_is_one_shot(db_session):
    attempt = service.ensure_attempt(
        db_session, workflow_key="campaign:test", item_id="793202812082",
        sku_code="PPS2441004051311",
        manifest={"slot": "new", "attributes": {"size": "130cm"}})
    same = service.ensure_attempt(
        db_session, workflow_key="campaign:test", item_id="793202812082",
        sku_code="PPS2441004051311",
        manifest={"slot": "new", "attributes": {"size": "130cm"}})
    assert same.id == attempt.id
    service.claim_write(db_session, attempt.id, request_id="req-1")
    with pytest.raises(ValueError, match="already_claimed"):
        service.claim_write(db_session, attempt.id, request_id="req-2")


def test_second_manifest_for_same_workflow_and_logical_sku_is_rejected(db_session):
    service.ensure_attempt(
        db_session, workflow_key="campaign:test-one", item_id="793202812082",
        sku_code="PPS2441004051311", manifest={"slot": "A"})
    with pytest.raises(ValueError, match="scope_conflict"):
        service.ensure_attempt(
            db_session, workflow_key="campaign:test-one", item_id="793202812082",
            sku_code="PPS2441004051311", manifest={"slot": "B"})


def test_failed_unknown_write_quarantines_target_slot(db_session):
    source = _slot(db_session, "9021", "active")
    target = _slot(db_session, "9022", "clean")
    service.reserve(db_session, target, workflow_key="campaign:switch")
    attempt = service.ensure_attempt(
        db_session, workflow_key="campaign:switch", item_id=source.taobao_item_id,
        sku_code=source.sku_code, source_slot_id=source.id,
        target_slot_id=target.id, manifest={"source": "9021", "target": "9022"})
    service.claim_write(db_session, attempt.id, request_id="req-unknown")
    service.finalize_switch(
        db_session, attempt.id, platform_write=True, success=False,
        result={"error": "browser_result_unknown"})
    assert source.state == "active"
    assert target.state == "cooling"


def test_seeded_custom_baseline_is_idempotent_and_never_tracks_later_price(db_session):
    sku = PricingSku(
        product_code="PC-CUSTOM", product_name="定制测试", sku="咨询规格",
        sku_code="PC-CUSTOM99", daily_price=Decimal("2000.00"),
        is_custom_placeholder=True)
    db_session.add_all([sku, PricingSkuPromo(
        sku_code="PC-CUSTOM99", taobao_item_id="800000000001",
        taobao_sku_id="600000000001")])
    db_session.commit()
    assert service.seed_active_slots(db_session)["created"] == 1
    sku.daily_price = Decimal("500.00")
    db_session.flush()
    assert service.seed_active_slots(db_session)["created"] == 0
    baseline = service.immutable_baseline(
        db_session, sku_code=sku.sku_code, item_id="800000000001",
        taobao_sku_id="600000000001")
    assert baseline == Decimal("2000.00")
    row = db_session.query(CampaignSkuSlot).filter_by(
        taobao_sku_id="600000000001").one()
    assert row.custom_min_final_price == Decimal("400.00")

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.sku_identity import SkuIdentity, SkuIdentityObservation, SkuPhysicalSlotProposal
from app.services import sku_identity_service


def _db():
    engine = create_engine("sqlite:///:memory:")
    SkuIdentity.__table__.create(engine)
    SkuIdentityObservation.__table__.create(engine)
    SkuPhysicalSlotProposal.__table__.create(engine)
    return Session(engine)


def test_migration_0147_is_linear_and_contains_all_ledger_tables():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0147_sku_identity_ledger.py"
    text = path.read_text(encoding="utf-8")
    assert 'revision = "0147"' in text
    assert 'down_revision = "0146"' in text
    for table in ("sku_identities", "sku_identity_observations", "sku_physical_slot_proposals"):
        assert table in text


def test_observations_append_and_identity_meaning_never_overwrites():
    db = _db()
    base = {
        "taobao_item_id": "793202812082", "taobao_sku_id": "6241447059625",
        "merchant_code": "PPS2441004051311", "sku_spec": "130cm 带高台",
        "sku_code": "PPS2441004051311", "product_code": "PPS2441",
        "daily_price": "6825.00", "sale_state": "on_sale",
    }
    first = sku_identity_service.observe(
        db, [base], evidence_source="official_product_export",
        evidence_sha256="a" * 64, observed_at=datetime(2026, 8, 31, tzinfo=timezone.utc))
    second = sku_identity_service.observe(
        db, [{**base, "daily_price": "6825.00"}], evidence_source="official_product_export",
        evidence_sha256="b" * 64, observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    conflict = sku_identity_service.observe(
        db, [{**base, "sku_spec": "different meaning"}], evidence_source="official_product_export",
        evidence_sha256="c" * 64, observed_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    row = db.execute(select(SkuIdentity)).scalar_one()
    observations = db.execute(select(SkuIdentityObservation).order_by(SkuIdentityObservation.id)).scalars().all()
    assert first["created"] == 1 and second["refreshed"] == 1 and conflict["conflicts"] == 1
    assert row.sku_spec == "130cm 带高台"
    assert row.conflict_detected is True
    assert [r.disposition for r in observations] == [
        "created", "observed_same_identity", "identity_conflict"]


def test_lift_desk_slot_stays_proposed_and_never_claims_live_sku_id():
    db = _db()
    result = sku_identity_service.ensure_lift_desk_proposal(
        db, authorization_ref="user:2026-08-31:lift-desk-pilot")
    row = db.execute(select(SkuPhysicalSlotProposal)).scalar_one()
    assert result["created"] is True
    assert row.target_option == "130cm 带高台升降桌"
    assert "备用" not in row.target_option
    assert row.parent_taobao_sku_id is None
    assert row.lifecycle_state == "proposed"
    assert row.product_save_status == "not_saved"
    assert row.campaign_signup_status == "not_submitted"
    assert row.proposed_fields["allowed_differences"] == ["option_name", "merchant_code"]


def test_failed_unsaved_stage_is_recorded_without_claiming_created_or_saved():
    db = _db()
    sku_identity_service.ensure_lift_desk_proposal(
        db, authorization_ref="user:2026-08-31:lift-desk-pilot")
    receipt = sku_identity_service.mark_lift_desk_stage_failed(db, result={
        "ok": False, "error": "sku_source_target_field_diff", "job_id": "job1",
        "platform_product_write": False,
        "field_copy": {"source_count": 1, "target_count": 1,
                       "diff": [{"index": 4, "source": "2个", "target": ""}],
                       "default_2000_eliminated": False},
    })
    row = db.execute(select(SkuPhysicalSlotProposal)).scalar_one()
    assert receipt["state"] == "staging_failed"
    assert row.product_create_status == "preview_failed"
    assert row.product_save_status == "not_saved"
    assert row.campaign_signup_status == "not_submitted"
    assert row.proposed_fields["stage_failures"][-1]["error"] == (
        "sku_source_target_field_diff")
    assert row.proposed_fields["stage_failures"][-1]["platform_product_write"] is False


def test_platform_snapshot_comparison_fails_closed_on_drift():
    db = _db()
    sku_identity_service.observe(db, [{
        "taobao_item_id": "793202812082", "taobao_sku_id": "6241447059625",
        "merchant_code": "PPS2441004051311", "sku_spec": "130cm 带高台",
    }], evidence_source="official_product_export", evidence_sha256="d" * 64)
    ok = sku_identity_service.assert_exact_platform_snapshot(db, [
        {"item_id": "793202812082", "sku_id": "6241447059625"}],
        item_ids={"793202812082"})
    drift = sku_identity_service.assert_exact_platform_snapshot(db, [
        {"item_id": "793202812082", "sku_id": "6241447059625"},
        {"item_id": "793202812082", "sku_id": "6241447059626"}],
        item_ids={"793202812082"})
    assert ok["ok"] is True
    assert drift["ok"] is False
    assert drift["missing_in_ledger"] == [("793202812082", "6241447059626")]


def test_current_artifact_snapshot_ignores_historical_old_sku_rows():
    db = _db()
    old = [{
        "taobao_item_id": "793202812082", "taobao_sku_id": "6241447059624",
        "merchant_code": "OLD-CODE", "sku_spec": "旧规格",
    }]
    current = [{
        "taobao_item_id": "793202812082", "taobao_sku_id": "6241447059625",
        "merchant_code": "CURRENT-CODE", "sku_spec": "当前规格",
    }]
    sku_identity_service.observe(
        db, old, evidence_source="official_product_export:old",
        evidence_sha256="1" * 64)
    sku_identity_service.observe(
        db, current, evidence_source="official_product_export:current",
        evidence_sha256="2" * 64)
    result = sku_identity_service.assert_current_platform_snapshot(
        db, [{"item_id": "793202812082", "sku_id": "6241447059625"}],
        item_ids={"793202812082"}, evidence_sha256="2" * 64)
    assert result["ok"] is True
    assert result["missing_in_current_evidence"] == []
    assert result["unexpected_in_current_evidence"] == []


def test_legacy_product_code_merchant_projection_is_corrected_with_history_kept():
    db = _db()
    old = {
        "taobao_item_id": "793202812082", "taobao_sku_id": "6241447059625",
        "merchant_code": "legacy-import-code", "sku_spec": "130cm 带高台",
        "sku_code": "PPS2441004051311", "product_code": "PPS24410040513",
    }
    sku_identity_service.observe(
        db, [old], evidence_source="erp_database_backfill:0147",
        evidence_sha256="e" * 64)
    result = sku_identity_service.observe(
        db, [{**old, "merchant_code": "PPS2441004051311"}],
        evidence_source="erp_database_backfill:0147-correction",
        evidence_sha256="f" * 64)
    row = db.execute(select(SkuIdentity)).scalar_one()
    history = db.execute(select(SkuIdentityObservation).order_by(
        SkuIdentityObservation.id)).scalars().all()
    assert result["conflicts"] == 0 and result["refreshed"] == 1
    assert row.merchant_code == "PPS2441004051311"
    assert [x.merchant_code for x in history] == ["legacy-import-code", "PPS2441004051311"]
    assert history[-1].disposition == "backfill_code_corrected"


def test_official_export_canonicalizes_backfill_spec_once_with_exact_codes():
    db = _db()
    old = {
        "taobao_item_id": "1036279566778", "taobao_sku_id": "6280283835626",
        "merchant_code": "PPS2633008032212", "sku_spec": "榉木柔光床-1.35米-榉木铺板",
        "sku_code": "PPS2633008032212", "product_code": "PPS26330080322",
    }
    official = {
        **old,
        "sku_spec": "床板材质:榉木;颜色分类:榉木柔光床-1.35米;",
    }
    sku_identity_service.observe(
        db, [old], evidence_source="erp_database_backfill:0147",
        evidence_sha256="1" * 64)
    result = sku_identity_service.observe(
        db, [official],
        evidence_source="official_product_export:campaign_execute",
        evidence_sha256="2" * 64)
    row = db.execute(select(SkuIdentity)).scalar_one()
    history = db.execute(select(SkuIdentityObservation).order_by(
        SkuIdentityObservation.id)).scalars().all()

    assert result == {"created": 0, "refreshed": 1, "conflicts": 0, "skipped": 0}
    assert row.sku_spec == official["sku_spec"]
    assert row.latest_evidence_source == "official_product_export:campaign_execute"
    assert row.conflict_detected is False
    assert history[-1].disposition == "backfill_spec_canonicalized"


def test_official_export_does_not_hide_spec_drift_after_official_identity_exists():
    db = _db()
    base = {
        "taobao_item_id": "1036279566778", "taobao_sku_id": "6280283835626",
        "merchant_code": "PPS2633008032212",
        "sku_spec": "床板材质:榉木;颜色分类:榉木柔光床-1.35米;",
        "sku_code": "PPS2633008032212", "product_code": "PPS26330080322",
    }
    sku_identity_service.observe(
        db, [base], evidence_source="official_product_export:campaign_execute",
        evidence_sha256="3" * 64)
    result = sku_identity_service.observe(
        db, [{**base, "sku_spec": "颜色分类:已被改成其他规格;"}],
        evidence_source="official_product_export:campaign_execute",
        evidence_sha256="4" * 64)
    row = db.execute(select(SkuIdentity)).scalar_one()

    assert result["conflicts"] == 1
    assert row.sku_spec == base["sku_spec"]
    assert row.conflict_detected is True

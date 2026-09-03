from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.cli import campaign_execute_plan7_final_closeout_v4 as cli
from app.models.campaign import CampaignPlan, CampaignPreparationBundle
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.sku_identity import SkuIdentity
from app.services import campaign_plan7_final_closeout_v4_service as service
from app.services import sku_identity_service
from app import dependencies


def _identity():
    return {
        "campaign_title": "超级立减", "campaign_id": None,
        "united_activity_id": None, "sign_record_id": None,
        "campaign_start": "2026-09-01 00:00:00",
        "campaign_end": "2026-09-05 23:59:59",
        "platform_activity_mode": "long_running_update",
        "official_rate": "10%",
    }


def _signup_rows(count=service.EXPECTED_SIGNUP_ROWS):
    sku_ids = [str(6070339397128 + i) for i in range(count - 2)]
    sku_ids += [service.PRIMARY_ACCESSORY_SKU_ID,
                service.EXTRA_ACCESSORY_SKU_ID]
    return [{
        "taobao_item_id": service.TARGET_ITEM_ID,
        "taobao_sku_id": sku_id,
        "sku_code": f"CODE-{i}", "price": 1000 + i,
        "is_placeholder": False,
    } for i, sku_id in enumerate(sku_ids)]


def _discount_rows():
    return [{
        "taobao_item_id": service.TARGET_ITEM_ID,
        "taobao_sku_id": str(6070339397134 + i),
        "sku_code": f"CODE-{i}", "deduct": 100,
    } for i in range(service.EXPECTED_DISCOUNT_ROWS)]


def _bundle(db_session, *, bundle_id="a" * 24):
    signup = _signup_rows()
    discount = _discount_rows()
    manifest = service.v3._manifest_sha(
        _identity(), service.POLICY_SHA256, signup, discount)
    row = CampaignPreparationBundle(
        id=bundle_id, plan_id=service.PLAN_ID,
        workflow_key=service.WORKFLOW_KEY, revision=5,
        state="ready_for_final_submission", prepared_by="test",
        source_sha256="b" * 64, policy_sha256=service.POLICY_SHA256,
        manifest_sha256=manifest, identity=_identity(),
        summary={
            "exact_item_scope_sha256": service.ITEM_SCOPE_SHA256,
            "global_blockers": [],
        },
        signup_rows=signup, discount_rows=discount,
        item_decisions=[
            {"taobao_item_id": service.TARGET_ITEM_ID, "state": "ready"},
            *[{"taobao_item_id": item, "state": "deferred_whole_item"}
              for item in sorted(service.DEFERRED_ITEM_IDS)],
        ],
        gate_results=[], evidence_snapshot_ids=[],
        execution_boundary={"platform_write": False},
        prepared_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _ledger(db_session, sku_id: str, fact: dict):
    meaning = {
        "taobao_item_id": service.TARGET_ITEM_ID,
        "taobao_sku_id": sku_id,
        "merchant_code": "PPS26330110226",
        "sku_spec": fact["sale_attr"], "sku_code": None,
        "product_code": "PPS26330110226",
        "is_custom_placeholder": False,
    }
    now = datetime.now(timezone.utc)
    db_session.add(SkuIdentity(
        **meaning,
        identity_sha256=sku_identity_service._hash(meaning),
        first_observed_at=now, last_observed_at=now,
        latest_sale_state="erp_backfill", latest_daily_price=Decimal("1000"),
        latest_evidence_source="erp_database_backfill:0147",
        latest_evidence_sha256="c" * 64, conflict_detected=False,
    ))


def test_v4_request_and_cli_are_fixed_to_one_incident():
    assert service.validate_request(service.request_payload()) is True
    changed = service.request_payload()
    changed["expected_extra_sku_id"] = "1"
    assert service.validate_request(changed) is False
    assert cli._FIXED_PAYLOAD == service.request_payload()
    assert cli._URL.endswith(
        "/execute-super-reduce-plan7-final-closeout-v4")
    assert dependencies.CAMPAIGN_PLAN7_FINAL_CLOSEOUT_V4_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)


def test_v4_invocation_is_durably_single_use(db_session):
    first, created = service._claim_invocation(db_session)
    second, replay = service._claim_invocation(db_session)

    assert created is True
    assert replay is False
    assert second.id == first.id
    assert first.operation == service.INVOCATION_OPERATION
    assert first.write_claimed is False
    assert first.automatic_retry_allowed is False


def test_v4_artifact_requires_exact_extra_and_critical_facts():
    source = _signup_rows(service.EXPECTED_SOURCE_SIGNUP_ROWS)
    # Build the source pair set so it excludes only the incident extra.
    source = [row for row in source
              if row["taobao_sku_id"] != service.EXTRA_ACCESSORY_SKU_ID]
    source.append({
        "taobao_item_id": service.TARGET_ITEM_ID,
        "taobao_sku_id": "6070339397141", "sku_code": "X"})
    records = [{
        "item_id": row["taobao_item_id"], "sku_id": row["taobao_sku_id"],
        "merchant_code": row.get("sku_code", "X"),
        "sale_attr": "x", "sku_price": 1, "stock": 1,
    } for row in source]
    records_by_sku = {row["sku_id"]: row for row in records}
    records_by_sku[service.PRIMARY_ACCESSORY_SKU_ID].update({
        "merchant_code": service.ACCESSORY_SKU_CODE,
        "sale_attr": service.ACCESSORY_SPEC_BEECH,
        "sku_price": 290, "stock": 100,
    })
    for sku_id, fact in {
        **service.PLACEHOLDER_FACTS,
        service.EXTRA_ACCESSORY_SKU_ID: service.EXTRA_ACCESSORY_FACT,
    }.items():
        records_by_sku[sku_id] = {
            "item_id": service.TARGET_ITEM_ID, "sku_id": sku_id,
            "merchant_code": "", "sale_attr": fact["sale_attr"],
            "sku_price": fact["sku_price"], "stock": fact["stock"],
        }
    records = list(records_by_sku.values())

    assert service._artifact_scope_error(records, source) is None
    records_by_sku[service.EXTRA_ACCESSORY_SKU_ID]["stock"] = 1
    assert service._artifact_scope_error(
        list(records_by_sku.values()), source)["critical_facts_match"] is False


def test_v4_mapping_repair_is_exact_and_idempotent(db_session):
    db_session.add(PricingSku(
        product_code="PPS26330110226", sku_code=service.ACCESSORY_SKU_CODE,
        sku="樱桃木静音床-配件-mini床头柜", list_price=Decimal("290"),
        daily_price=Decimal("217.50"), is_custom_placeholder=False,
    ))
    db_session.add(PricingSkuPromo(
        sku_code=service.ACCESSORY_SKU_CODE,
        taobao_item_id=service.TARGET_ITEM_ID,
        taobao_sku_id=service.PRIMARY_ACCESSORY_SKU_ID,
        alt_taobao_sku_ids=[], taobao_activity_price=Decimal("217.50"),
    ))
    for sku_id, fact in service.PLACEHOLDER_FACTS.items():
        _ledger(db_session, sku_id, fact)
    db_session.commit()

    first = service._repair_mapping(
        db_session, evidence_sha256=service.OFFICIAL_EXPORT_SHA256)
    db_session.commit()
    second = service._repair_mapping(
        db_session, evidence_sha256=service.OFFICIAL_EXPORT_SHA256)

    assert first["accessory_alt_added"] is True
    assert second["accessory_alt_added"] is False
    promo = db_session.query(PricingSkuPromo).filter_by(
        sku_code=service.ACCESSORY_SKU_CODE).one()
    assert promo.alt_taobao_sku_ids == [service.EXTRA_ACCESSORY_SKU_ID]
    rows = db_session.query(SkuIdentity).filter(
        SkuIdentity.taobao_sku_id.in_(service.PLACEHOLDER_FACTS)).all()
    assert all(row.is_custom_placeholder for row in rows)
    assert {row.sku_code for row in rows} == {
        fact["merchant_code"] for fact in service.PLACEHOLDER_FACTS.values()}


def test_v4_push_context_rejects_any_bundle_drift(db_session):
    plan = CampaignPlan(
        id=service.PLAN_ID, workflow_key=service.WORKFLOW_KEY,
        name="p7", campaign_type="super_reduce",
        platform_activity_mode="long_running_update",
        qn_campaign_title="超级立减", status="resume_executing",
        start_at=datetime.now(timezone.utc), end_at=datetime.now(timezone.utc),
    )
    db_session.add(plan)
    bundle = _bundle(db_session)
    context = {
        "bundle_id": bundle.id, "source_sha256": bundle.source_sha256,
        "policy_sha256": bundle.policy_sha256,
        "manifest_sha256": bundle.manifest_sha256,
        "item_scope_sha256": service.ITEM_SCOPE_SHA256,
    }
    identity = {
        "ok": True, "checked_items": 1,
        "checked_skus": service.EXPECTED_SIGNUP_ROWS,
        "official_skus": service.EXPECTED_SIGNUP_ROWS,
        "artifact": {"sha256": service.OFFICIAL_EXPORT_SHA256},
    }

    ok, _ = service.validate_push_context(
        db_session, plan, exact_item_scope={service.TARGET_ITEM_ID},
        policy_sha256=service.POLICY_SHA256,
        prepared_bundle_context=context, official_identity=identity)
    context["manifest_sha256"] = "0" * 64
    drifted, _ = service.validate_push_context(
        db_session, plan, exact_item_scope={service.TARGET_ITEM_ID},
        policy_sha256=service.POLICY_SHA256,
        prepared_bundle_context=context, official_identity=identity)

    assert ok is True
    assert drifted is False

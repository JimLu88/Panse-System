from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan, CampaignSkuSlot
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.sku_identity import SkuIdentity, SkuIdentityObservation
from app.services import (
    campaign_plan8_sku_mapping_repair_service as repair,
    campaign_sku_slot_service,
    delisted_sku_service,
    settings_service,
    sku_identity_service,
)


def _seed(db):
    db.add(CampaignPlan(
        id=repair.PLAN_ID, name="超级88现货", campaign_type="big88", tier="big",
        workflow_key=repair.WORKFLOW_KEY, status="alarmed",
    ))
    settings_service.set_value(
        db, "delisted_skuids",
        '["1111111111111", "6287431318354", "6287431318356", '
        '"6287431318358", "6287431318360"]')
    for row in repair.ROWS:
        db.add(PricingSku(
            product_code=row["source"][:14], product_name="测试产品",
            taobao_title="测试淘宝标题", sku=f"source-{row['source']}",
            sku_code=row["source"], size_category="中型",
            size_info="宽度：1250mm；长度：2100mm；高度：880mm",
            is_custom_placeholder=False,
        ))
        db.add(PricingSkuPromo(
            sku_code=row["source"], taobao_item_id=row["item_id"],
            taobao_url=f"https://item.taobao.com/item.htm?id={row['item_id']}",
            taobao_sku_id=f"source-{row['sku_id']}", alt_taobao_sku_ids=[],
        ))
        old_meaning = repair._meaning(row, corrected=False)
        digest = sku_identity_service._hash(old_meaning)
        db.add(SkuIdentity(
            **old_meaning, identity_sha256=digest,
            first_observed_at=datetime.now(timezone.utc),
            last_observed_at=datetime.now(timezone.utc),
            latest_evidence_source="erp_database_backfill:0147",
            latest_evidence_sha256="a" * 64, conflict_detected=False,
        ))
        if row["old_code"]:
            db.add(PricingSku(
                product_code=row["code"][:14], sku=f"legacy-{row['sku_id']}",
                sku_code=row["old_code"], daily_price=Decimal(row["list"]),
                is_custom_placeholder=True,
            ))
            db.add(PricingSkuPromo(
                sku_code=row["old_code"], taobao_item_id=row["item_id"],
                taobao_sku_id=row["sku_id"], alt_taobao_sku_ids=[],
            ))
            db.add(CampaignSkuSlot(
                sku_code=row["old_code"], taobao_item_id=row["item_id"],
                taobao_sku_id=row["sku_id"],
                physical_slot_code=f"{row['old_code']}-LEGACY-1-{row['sku_id']}",
                state="active", attribute_sha256=campaign_sku_slot_service.attribute_sha256({
                    "product_code": row["code"][:14], "sku": f"legacy-{row['sku_id']}",
                    "size_info": "",
                }), baseline_daily_price=Decimal(row["list"]),
                custom_min_final_price=Decimal(row["list"]) * Decimal("0.20"),
            ))
    db.commit()


def test_fixed_payload_is_exact_and_stable():
    assert repair.SCOPE_SHA256 == (
        "305c17ca1097fade9614da428fd947ea17925fd49e418335ff05e000d08292bd")
    assert repair.fixed_payload()["official_product_export_sha256"] == (
        "fb9e552254f29f8e022f799edd5a6a01b7dfc6653112dba3ee5286bb4270b984")
    assert len(repair.ROWS) == 8
    assert repair.FALSE_DELISTED_SKU_IDS == {
        "6287431318354", "6287431318356", "6287431318358", "6287431318360"}


def test_one_shot_repair_creates_eight_normal_skus_and_retires_four_aliases(db_session):
    _seed(db_session)
    result = repair.execute(db_session)

    assert result["ok"] is True
    assert result["state"] == "completed"
    target_codes = {row["code"] for row in repair.ROWS}
    created = db_session.execute(select(PricingSku).where(
        PricingSku.sku_code.in_(target_codes))).scalars().all()
    assert len(created) == 8
    assert all(not row.is_custom_placeholder for row in created)
    assert {str(row.list_price) for row in created} == {
        row["list"] for row in repair.ROWS}
    assert {str(row.daily_price) for row in created} == {
        row["daily"] for row in repair.ROWS}
    promos = db_session.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code.in_(target_codes))).scalars().all()
    assert len(promos) == 8
    assert {(p.taobao_item_id, p.taobao_sku_id) for p in promos} == {
        (row["item_id"], row["sku_id"]) for row in repair.ROWS}
    costs = db_session.execute(select(PricingSkuCosts).where(
        PricingSkuCosts.sku_code.in_(target_codes))).scalars().all()
    assert len(costs) == 8

    legacy_codes = {row["old_code"] for row in repair.ROWS if row["old_code"]}
    legacy_promos = db_session.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code.in_(legacy_codes))).scalars().all()
    assert all(p.taobao_item_id is None and p.taobao_sku_id is None
               and p.alt_taobao_sku_ids == [] for p in legacy_promos)
    identities = db_session.execute(select(SkuIdentity).where(
        SkuIdentity.taobao_sku_id.in_([row["sku_id"] for row in repair.ROWS])
    )).scalars().all()
    assert {row.sku_code for row in identities} == target_codes
    assert all(not row.is_custom_placeholder and not row.conflict_detected
               for row in identities)
    observations = db_session.execute(select(SkuIdentityObservation).where(
        SkuIdentityObservation.disposition == "authorized_correction"
    )).scalars().all()
    assert len(observations) == 8
    slots = db_session.execute(select(CampaignSkuSlot).where(
        CampaignSkuSlot.taobao_sku_id.in_([row["sku_id"] for row in repair.ROWS])
    )).scalars().all()
    assert len(slots) == 8
    assert {slot.sku_code for slot in slots} == target_codes
    assert all(slot.custom_min_final_price is None for slot in slots)
    assert delisted_sku_service.get_delisted(db_session) == {"1111111111111"}

    second = repair.execute(db_session)
    assert second["ok"] is True and second["already_claimed"] is True
    assert db_session.execute(select(CampaignExecutionAttempt)).scalars().all().__len__() == 1


def test_preview_proves_78_rows_and_rolls_everything_back(db_session):
    _seed(db_session)
    original_build = repair.campaign_service.build_signup_rows
    repair.campaign_service.build_signup_rows = lambda db, plan: ([
        {
            "taobao_item_id": item_id,
            "taobao_sku_id": str(7000000000000 + index),
            "is_placeholder": index < repair.EXPECTED_PENDING_CUSTOM_ROW_COUNT,
            "price": "100.00",
        }
        for index, item_id in enumerate(
            item_id
            for item_id in sorted(repair.EXPECTED_PENDING_ITEM_IDS)
            for _ in range(13)
        )
    ], {"test": True})
    try:
        result = repair.preview(db_session)
    finally:
        repair.campaign_service.build_signup_rows = original_build

    assert result["ok"] is True
    assert result["database_write"] is False
    assert result["pending_row_count"] == 78
    assert result["pending_custom_row_count"] == 18
    assert db_session.execute(select(CampaignExecutionAttempt)).scalars().all() == []
    assert db_session.execute(select(PricingSku).where(
        PricingSku.sku_code.in_([row["code"] for row in repair.ROWS])
    )).scalars().all() == []

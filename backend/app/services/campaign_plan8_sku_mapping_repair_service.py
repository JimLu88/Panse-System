"""One-shot repair for eight user-approved ordinary Plan-8 SKU mappings."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan, CampaignSkuSlot
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.settings import SystemSetting
from app.models.sku_identity import SkuIdentity
from app.services import (
    campaign_execution_service,
    campaign_service,
    campaign_sku_slot_service,
    delisted_sku_service,
    pricing_calc_service,
    settings_service,
    sku_identity_service,
)


WORKFLOW_KEY = "campaign:super88:49462:49469"
PLAN_ID = 8
OPERATION = "plan8_sku_mapping_repair"
OFFICIAL_PRODUCT_EXPORT_SHA256 = (
    "fb9e552254f29f8e022f799edd5a6a01b7dfc6653112dba3ee5286bb4270b984"
)
AUTHORIZATION_REF = "user_approved_eight_sku_mapping:2026-09-02"
EXPECTED_PENDING_ITEM_IDS = {
    "1036279566778",
    "1036312802226",
    "1074244132390",
    "837902729785",
    "841201084787",
    "917179577721",
}
EXPECTED_PENDING_ROW_COUNT = 78
EXPECTED_PENDING_CUSTOM_ROW_COUNT = 18
FALSE_DELISTED_SKU_IDS = {
    "6287431318354",
    "6287431318356",
    "6287431318358",
    "6287431318360",
}

ROWS = (
    {"item_id": "1036279566778", "sku_id": "6234601898881",
     "code": "PPS2633008032223", "source": "PPS2633008032219",
     "sku": "榉木大靠背床（无灯）-1.2米-松木铺板",
     "spec": "床板材质:松木;颜色分类:榉木大板床-无灯-1.2米;",
     "list": "6940.00", "daily": "5205.00", "wood": "2010.00",
     "pack": "140.00", "bed_board": "160.00", "soft_pack": "0.00",
     "other": "40.00", "old_code": None, "old_custom": False},
    {"item_id": "1036279566778", "sku_id": "6234601898883",
     "code": "PPS2633008032224", "source": "PPS2633008032220",
     "sku": "榉木大靠背床（无灯）-1.35米-松木铺板",
     "spec": "床板材质:松木;颜色分类:榉木大板床-无灯-1.35米;",
     "list": "7060.00", "daily": "5295.00", "wood": "2040.00",
     "pack": "140.00", "bed_board": "180.00", "soft_pack": "0.00",
     "other": "40.00", "old_code": None, "old_custom": False},
    {"item_id": "1036279566778", "sku_id": "6234601898885",
     "code": "PPS2633008032225", "source": "PPS2633008032221",
     "sku": "榉木大靠背床（无灯）-1.5米-松木铺板",
     "spec": "床板材质:松木;颜色分类:榉木大板床-无灯-1.5米;",
     "list": "7350.00", "daily": "5512.50", "wood": "2120.00",
     "pack": "150.00", "bed_board": "180.00", "soft_pack": "0.00",
     "other": "60.00", "old_code": None, "old_custom": False},
    {"item_id": "1036279566778", "sku_id": "6234601898887",
     "code": "PPS2633008032226", "source": "PPS2633008032222",
     "sku": "榉木大靠背床（无灯）-1.8米-松木铺板",
     "spec": "床板材质:松木;颜色分类:榉木大板床-无灯-1.8米;",
     "list": "7630.00", "daily": "5722.50", "wood": "2210.00",
     "pack": "150.00", "bed_board": "200.00", "soft_pack": "0.00",
     "other": "60.00", "old_code": None, "old_custom": False},
    {"item_id": "1074244132390", "sku_id": "6287431318354",
     "code": "PPS2633010022523", "source": "PPS2633010022519",
     "sku": "樱桃木齐边床（软包款）-1.2米-松木铺板",
     "spec": "床板材质:松木;颜色分类:齐边床-软包款-1.2米;",
     "list": "7040.00", "daily": "5280.00", "wood": "1610.00",
     "pack": "140.00", "bed_board": "160.00", "soft_pack": "440.00",
     "other": "40.00", "old_code": "PPS2633010022595", "old_custom": True},
    {"item_id": "1074244132390", "sku_id": "6287431318356",
     "code": "PPS2633010022524", "source": "PPS2633010022520",
     "sku": "樱桃木齐边床（软包款）-1.35米-松木铺板",
     "spec": "床板材质:松木;颜色分类:齐边床-软包款-1.35米;",
     "list": "7240.00", "daily": "5430.00", "wood": "1640.00",
     "pack": "140.00", "bed_board": "180.00", "soft_pack": "470.00",
     "other": "40.00", "old_code": "PPS2633010022594", "old_custom": True},
    {"item_id": "1074244132390", "sku_id": "6287431318358",
     "code": "PPS2633010022525", "source": "PPS2633010022521",
     "sku": "樱桃木齐边床（软包款）-1.5米-松木铺板",
     "spec": "床板材质:松木;颜色分类:齐边床-软包款-1.5米;",
     "list": "7480.00", "daily": "5610.00", "wood": "1670.00",
     "pack": "150.00", "bed_board": "180.00", "soft_pack": "500.00",
     "other": "61.00", "old_code": "PPS2633010022593", "old_custom": True},
    {"item_id": "1074244132390", "sku_id": "6287431318360",
     "code": "PPS2633010022526", "source": "PPS2633010022522",
     "sku": "樱桃木齐边床（软包款）-1.8米-松木铺板",
     "spec": "床板材质:松木;颜色分类:齐边床-软包款-1.8米;",
     "list": "7810.00", "daily": "5857.50", "wood": "1750.00",
     "pack": "150.00", "bed_board": "200.00", "soft_pack": "531.00",
     "other": "60.00", "old_code": "PPS2633010022592", "old_custom": True},
)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


SCOPE_SHA256 = _hash({
    "workflow_key": WORKFLOW_KEY,
    "plan_id": PLAN_ID,
    "official_product_export_sha256": OFFICIAL_PRODUCT_EXPORT_SHA256,
    "authorization_ref": AUTHORIZATION_REF,
    "false_delisted_sku_ids": sorted(FALSE_DELISTED_SKU_IDS),
    "rows": ROWS,
})


def fixed_payload() -> dict:
    return {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "operation": OPERATION,
        "scope_sha256": SCOPE_SHA256,
        "official_product_export_sha256": OFFICIAL_PRODUCT_EXPORT_SHA256,
        "authorization_ref": AUTHORIZATION_REF,
    }


def _meaning(row: dict, *, corrected: bool) -> dict:
    code = row["code"] if corrected else row["old_code"]
    merchant = code if code else "PPS26330080322"
    return {
        "taobao_item_id": row["item_id"],
        "taobao_sku_id": row["sku_id"],
        "merchant_code": merchant,
        "sku_spec": row["spec"],
        "sku_code": code,
        "product_code": row["code"][:14],
        "is_custom_placeholder": False if corrected else row["old_custom"],
    }


def _preflight(db: Session) -> dict:
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    )).scalar_one_or_none()
    if plan is None or plan.status != "alarmed":
        raise ValueError("plan8_mapping_repair_plan_drift")
    target_codes = [row["code"] for row in ROWS]
    if db.execute(select(PricingSku.id).where(
            PricingSku.sku_code.in_(target_codes))).first():
        raise ValueError("plan8_mapping_repair_target_sku_exists")
    if db.execute(select(PricingSkuPromo.id).where(
            PricingSkuPromo.sku_code.in_(target_codes))).first():
        raise ValueError("plan8_mapping_repair_target_promo_exists")
    delisted = delisted_sku_service.get_delisted(db)
    target_sku_ids = {row["sku_id"] for row in ROWS}
    if delisted & target_sku_ids != FALSE_DELISTED_SKU_IDS:
        raise ValueError("plan8_mapping_repair_false_delisted_scope_drift")
    for row in ROWS:
        source = db.execute(select(PricingSku).where(
            PricingSku.sku_code == row["source"])).scalar_one_or_none()
        if source is None or source.is_custom_placeholder:
            raise ValueError("plan8_mapping_repair_source_drift")
        identity = db.execute(select(SkuIdentity).where(
            SkuIdentity.taobao_item_id == row["item_id"],
            SkuIdentity.taobao_sku_id == row["sku_id"],
        )).scalar_one_or_none()
        expected = _meaning(row, corrected=False)
        if identity is None or any(getattr(identity, key) != value
                                   for key, value in expected.items()):
            raise ValueError("plan8_mapping_repair_identity_drift")
        if row["old_code"]:
            old_sku = db.execute(select(PricingSku).where(
                PricingSku.sku_code == row["old_code"])).scalar_one_or_none()
            old_promo = db.execute(select(PricingSkuPromo).where(
                PricingSkuPromo.sku_code == row["old_code"])).scalar_one_or_none()
            if (old_sku is None or not old_sku.is_custom_placeholder
                    or old_promo is None
                    or old_promo.taobao_item_id != row["item_id"]
                    or old_promo.taobao_sku_id != row["sku_id"]):
                raise ValueError("plan8_mapping_repair_legacy_alias_drift")
    return {
        "ok": True,
        "plan_status": plan.status,
        "row_count": len(ROWS),
        "false_delisted_sku_ids": sorted(FALSE_DELISTED_SKU_IDS),
    }


def _clear_false_delisted(db: Session) -> dict:
    setting = db.execute(select(SystemSetting).where(
        SystemSetting.key == "delisted_skuids"
    ).with_for_update()).scalar_one_or_none()
    if setting is None or setting.is_secret or not setting.value_plain:
        raise ValueError("plan8_mapping_repair_delisted_setting_missing")
    try:
        current = {
            str(value).strip() for value in json.loads(setting.value_plain)
            if str(value).strip()
        }
    except (TypeError, ValueError) as exc:
        raise ValueError("plan8_mapping_repair_delisted_setting_invalid") from exc
    target_sku_ids = {row["sku_id"] for row in ROWS}
    if current & target_sku_ids != FALSE_DELISTED_SKU_IDS:
        raise ValueError("plan8_mapping_repair_false_delisted_scope_drift")
    remaining = current - FALSE_DELISTED_SKU_IDS
    settings_service.set_value(
        db,
        "delisted_skuids",
        json.dumps(sorted(remaining), ensure_ascii=False),
        description="下架SKU登记(报名自动排除: 在售全报、下架不报)",
    )
    return {
        "removed": sorted(FALSE_DELISTED_SKU_IDS),
        "before_count": len(current),
        "after_count": len(remaining),
        "official_product_export_sha256": OFFICIAL_PRODUCT_EXPORT_SHA256,
    }


def _new_pricing_row(db: Session, row: dict) -> tuple[PricingSku, PricingSkuPromo]:
    source = db.execute(select(PricingSku).where(
        PricingSku.sku_code == row["source"])).scalar_one()
    external = sum(Decimal(row[key]) for key in ("bed_board", "soft_pack", "other"))
    sku = PricingSku(
        product_code=row["code"][:14], product_name=source.product_name,
        taobao_title=source.taobao_title, sku=row["sku"], sku_code=row["code"],
        size_category=source.size_category, size_info=source.size_info,
        product_weight_kg=source.product_weight_kg,
        packaged_weight_kg=source.packaged_weight_kg,
        product_volume_m3=source.product_volume_m3,
        packaged_volume_m3=source.packaged_volume_m3,
        wood_cost=Decimal(row["wood"]), packaging_cost=Decimal(row["pack"]),
        external_parts_cost=external, logistics_cost=Decimal("300.00"),
        install_cost=Decimal("50.00"), factory_cost_override=False,
        base_list=Decimal("0.4000"), base_small=Decimal("0.8600"),
        base_mid=Decimal("0.8800"), base_big=Decimal("0.9000"),
        image_url=source.image_url,
        remark=("2026-09-02用户确认普通SKU映射；官方标价反算成本；"
                "同款木作/包装/床铺板/软包，结构差额单列other_cost"),
        is_custom_placeholder=False,
    )
    pricing_calc_service.recompute(sku)
    if (sku.list_price != Decimal(row["list"])
            or sku.daily_price != Decimal(row["daily"])):
        raise ValueError("plan8_mapping_repair_price_derivation_drift")
    db.add(sku)
    db.add(PricingSkuCosts(
        sku_code=row["code"], soft_pack=Decimal(row["soft_pack"]),
        bed_board=Decimal(row["bed_board"]), other_cost=Decimal(row["other"]),
        other_desc=("计划8八SKU修复：官方标价反算后的结构差额；"
                    f"证据SHA256={OFFICIAL_PRODUCT_EXPORT_SHA256}"),
    ))
    source_promo = db.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code == row["source"])).scalar_one()
    promo = PricingSkuPromo(
        sku_code=row["code"], taobao_item_id=row["item_id"],
        taobao_url=source_promo.taobao_url, taobao_sku_id=row["sku_id"],
        alt_taobao_sku_ids=[],
    )
    pricing_calc_service.recompute_promo(
        promo, sku, pricing_calc_service.get_promo_params(db))
    db.add(promo)
    return sku, promo


def _repair(db: Session) -> dict:
    evidence_source = "authorized_identity_correction:plan8:2026-09-02"
    delisted_correction = _clear_false_delisted(db)
    created = []
    retired = []
    corrections = []
    for row in ROWS:
        sku, promo = _new_pricing_row(db, row)
        db.flush()
        corrections.append(sku_identity_service.authorize_canonical_correction(
            db, expected=_meaning(row, corrected=False),
            corrected=_meaning(row, corrected=True),
            evidence_source=evidence_source,
            evidence_sha256=OFFICIAL_PRODUCT_EXPORT_SHA256,
            authorization_ref=AUTHORIZATION_REF,
            daily_price=sku.daily_price,
        ))
        if row["old_code"]:
            old_sku = db.execute(select(PricingSku).where(
                PricingSku.sku_code == row["old_code"])).scalar_one()
            old_promo = db.execute(select(PricingSkuPromo).where(
                PricingSkuPromo.sku_code == row["old_code"])).scalar_one()
            old_promo.taobao_item_id = None
            old_promo.taobao_sku_id = None
            old_promo.alt_taobao_sku_ids = []
            old_sku.remark = (
                f"2026-09-02退役历史别名；原SKU {row['sku_id']} 已授权迁移至 {row['code']}"
            )
            slot = db.execute(select(CampaignSkuSlot).where(
                CampaignSkuSlot.taobao_sku_id == row["sku_id"]
            ).with_for_update()).scalar_one_or_none()
            if slot is None or slot.sku_code != row["old_code"]:
                raise ValueError("plan8_mapping_repair_legacy_slot_drift")
            slot.sku_code = row["code"]
            slot.attribute_sha256 = campaign_sku_slot_service.attribute_sha256({
                "product_code": sku.product_code,
                "sku": sku.sku or "",
                "size_info": sku.size_info or "",
            })
            slot.baseline_daily_price = sku.daily_price
            slot.custom_min_final_price = None
            slot.last_workflow_key = WORKFLOW_KEY
            retired.append(row["old_code"])
        created.append({
            "item_id": row["item_id"], "sku_id": row["sku_id"],
            "sku_code": row["code"], "list_price": str(sku.list_price),
            "daily_price": str(sku.daily_price),
            "small_promo": str(sku.small_promo),
            "mid_promo": str(sku.mid_promo), "big_promo": str(sku.big_promo),
            "taobao_activity_price": str(promo.taobao_activity_price),
        })
    seed = campaign_sku_slot_service.seed_active_slots(db)
    db.flush()
    return {
        "created": created, "retired_aliases": sorted(retired),
        "identity_corrections": corrections, "slot_seed": seed,
        "delisted_correction": delisted_correction,
    }


def preview(db: Session) -> dict:
    """Simulate the full repair and prove the resulting six-item scope.

    The caller receives production-shaped evidence, while every simulated
    mutation is rolled back.  This is the mandatory gate before consuming the
    one-shot write claim.
    """
    try:
        preflight = _preflight(db)
        repair = _repair(db)
        plan = db.get(CampaignPlan, PLAN_ID)
        rows, stats = campaign_service.build_signup_rows(db, plan)
        pending = [
            row for row in rows
            if str(row.get("taobao_item_id") or "") in EXPECTED_PENDING_ITEM_IDS
        ]
        item_ids = {
            str(row.get("taobao_item_id") or "") for row in pending
        }
        sku_ids = [str(row.get("taobao_sku_id") or "") for row in pending]
        custom_rows = [row for row in pending if row.get("is_placeholder") is True]
        if (
            item_ids != EXPECTED_PENDING_ITEM_IDS
            or len(pending) != EXPECTED_PENDING_ROW_COUNT
            or len(set(sku_ids)) != EXPECTED_PENDING_ROW_COUNT
            or not all(value.isdigit() for value in sku_ids)
            or len(custom_rows) != EXPECTED_PENDING_CUSTOM_ROW_COUNT
            or "1038725569412" in item_ids
            or "793202812082" in item_ids
        ):
            raise ValueError("plan8_mapping_repair_preview_scope_drift")
        return {
            "ok": True,
            "database_write": False,
            "preflight": preflight,
            "simulated_repair": repair,
            "pending_item_ids": sorted(item_ids),
            "pending_row_count": len(pending),
            "pending_custom_row_count": len(custom_rows),
            "pending_normal_row_count": len(pending) - len(custom_rows),
            "pending_scope_sha256": campaign_execution_service.scope_sha256(
                identity=campaign_service.campaign_identity(plan),
                rows=pending,
                policy_sha256="preview_after_mapping_repair",
            ),
            "generator_stats": stats,
        }
    finally:
        db.rollback()


def execute(db: Session) -> dict:
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
        CampaignExecutionAttempt.scope_sha256 == SCOPE_SHA256,
    )).scalar_one_or_none()
    if existing:
        return {
            "ok": existing.state == "completed", "already_claimed": True,
            "attempt_id": existing.id, "state": existing.state,
            "result": existing.result_summary,
        }
    preflight = _preflight(db)
    attempt = CampaignExecutionAttempt(
        id=secrets.token_hex(12), plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=OPERATION, scope_sha256=SCOPE_SHA256, state="executing",
        write_claimed=True, write_claimed_at=datetime.now(timezone.utc),
        platform_write_observed=False, automatic_retry_allowed=False,
        request_id=f"plan8-map-{secrets.token_hex(6)}",
        last_step="claim_committed",
        result_summary={
            "preflight": preflight,
            "execution_boundary": {"database_write": True, "platform_write": False},
        },
    )
    db.add(attempt)
    db.commit()
    attempt_id = attempt.id
    try:
        result = _repair(db)
        attempt = db.execute(select(CampaignExecutionAttempt).where(
            CampaignExecutionAttempt.id == attempt_id).with_for_update()).scalar_one()
        attempt.state = "completed"
        attempt.last_step = "database_readback_verified"
        attempt.result_summary = {
            "preflight": preflight, "repair": result,
            "official_product_export_sha256": OFFICIAL_PRODUCT_EXPORT_SHA256,
            "authorization_ref": AUTHORIZATION_REF,
            "execution_boundary": {"database_write": True, "platform_write": False},
        }
        db.commit()
        return {"ok": True, "attempt_id": attempt_id, "state": "completed",
                "result": attempt.result_summary}
    except Exception as exc:
        db.rollback()
        failed = db.execute(select(CampaignExecutionAttempt).where(
            CampaignExecutionAttempt.id == attempt_id).with_for_update()).scalar_one()
        failed.state = "failed_no_retry"
        failed.last_step = "database_repair_failed"
        failed.error_code = type(exc).__name__
        failed.result_summary = {
            "preflight": preflight, "error": str(exc),
            "execution_boundary": {"database_write": False, "platform_write": False},
        }
        db.commit()
        return {"ok": False, "attempt_id": attempt_id,
                "state": "failed_no_retry", "error": str(exc)}

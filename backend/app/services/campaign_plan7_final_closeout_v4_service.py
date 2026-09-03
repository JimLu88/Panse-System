"""Incident-scoped V4 closeout for the final Super Reduce plan-7 item.

V4 recovers one immutable official product export, corrects only the proven
one-to-many SKU mapping and two blank-code placeholder ledger projections,
compiles a fresh immutable bundle, and then permits one existing guarded
signup execution.  It never creates a product export or retries a write.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import (
    CampaignExecutionAttempt,
    CampaignPlan,
    CampaignPreparationBundle,
)
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.sku_identity import SkuIdentity
from app.services import (
    campaign_execution_service,
    campaign_plan7_final_closeout_service as v3,
    campaign_policy_service,
    campaign_preparation_service,
    campaign_recon_service,
    campaign_service,
    sku_identity_service,
    web_agent_service,
)


WORKFLOW_KEY = v3.WORKFLOW_KEY
PLAN_ID = v3.PLAN_ID
EXPECTED_STATUS = v3.EXPECTED_STATUS
SOURCE_BUNDLE_ID = v3.BUNDLE_ID
SOURCE_SHA256 = v3.SOURCE_SHA256
POLICY_SHA256 = v3.POLICY_SHA256
SOURCE_MANIFEST_SHA256 = v3.MANIFEST_SHA256
ITEM_SCOPE_SHA256 = v3.ITEM_SCOPE_SHA256
TARGET_ITEM_ID = v3.TARGET_ITEM_ID
DEFERRED_ITEM_IDS = v3.DEFERRED_ITEM_IDS
PRESERVED_ACTIVE_ITEM_IDS = v3.PRESERVED_ACTIVE_ITEM_IDS
EXEMPT_ITEM_IDS = v3.EXEMPT_ITEM_IDS
EXPECTED_SOURCE_SIGNUP_ROWS = v3.EXPECTED_SIGNUP_ROWS
EXPECTED_SIGNUP_ROWS = 14
EXPECTED_SOURCE_DISCOUNT_ROWS = v3.EXPECTED_DISCOUNT_ROWS
EXPECTED_DISCOUNT_ROWS = 10
EXECUTION_SOURCE = "campaign_super_reduce_plan7_final_closeout_v4"
RECOVERY_ID = "plan7-final-closeout-official-sku-scope-v4"
EXPECTED_WEB_AGENT_COMMIT = v3.EXPECTED_WEB_AGENT_COMMIT
OFFICIAL_EXPORT_SHA256 = (
    "a9ec3974dd1fc251f2b3dd7163fa4fcd8f8b08fd710424d85b1a7c0b723e0afd"
)
OFFICIAL_EXPORT_RECORD = {
    "id": "330012453",
    "sourceFileName": "2215699812811_edit_custom_1788444263641",
    "rowCount": 1,
    "failedRowCount": 0,
    "gmtCreate": "2026-09-03 22:04:24",
    "expected_sha256": OFFICIAL_EXPORT_SHA256,
}
PRIMARY_ACCESSORY_SKU_ID = "6280268983408"
EXTRA_ACCESSORY_SKU_ID = "6280268983409"
ACCESSORY_SKU_CODE = "PPS2633011022619"
ACCESSORY_SPEC_BEECH = "床板材质:榉木;颜色分类:mini床头柜-配件;"
ACCESSORY_SPEC_PINE = "床板材质:松木;颜色分类:mini床头柜-配件;"
PLACEHOLDER_FACTS = {
    "6070339397130": {
        "merchant_code": "PPS2633011022697",
        "sale_attr": "床板材质:榉木;颜色分类:尺寸微定制;",
        "sku_price": Decimal("1000"), "stock": 6,
    },
    "6070339397131": {
        "merchant_code": "PPS2633011022696",
        "sale_attr": "床板材质:松木;颜色分类:尺寸微定制;",
        "sku_price": Decimal("1000"), "stock": 10,
    },
}
EXTRA_ACCESSORY_FACT = {
    "merchant_code": ACCESSORY_SKU_CODE,
    "sale_attr": ACCESSORY_SPEC_PINE,
    "sku_price": Decimal("290"), "stock": 0,
}
AUTHORIZATION_REF = (
    "user-directed-plan7-v4-official-export-repair-2026-09-03"
)
INVOCATION_OPERATION = "plan7_closeout_v4"


def _boundary(*, platform_read: bool = False,
              platform_write: bool | None = False) -> dict:
    return {
        "plan_scoped_only": True,
        "bundle_scoped_only": True,
        "platform_read": platform_read,
        "platform_write": platform_write,
        "account_action": bool(platform_write),
        "price_change": False,
        "sku_rotation": False,
        "withdraw_pause_remove": False,
        "touches_plan8": False,
        "notification": platform_write is not False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, *, platform_read: bool = False, **detail) -> dict:
    return {
        "ok": False, "error": error, **detail,
        "execution_boundary": _boundary(platform_read=platform_read),
    }


def request_payload() -> dict:
    return {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "expected_status": EXPECTED_STATUS,
        "source_bundle_id": SOURCE_BUNDLE_ID,
        "expected_source_sha256": SOURCE_SHA256,
        "expected_policy_sha256": POLICY_SHA256,
        "expected_source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "expected_item_scope_sha256": ITEM_SCOPE_SHA256,
        "recovery_id": RECOVERY_ID,
        "expected_web_agent_commit": EXPECTED_WEB_AGENT_COMMIT,
        "official_export_record_id": OFFICIAL_EXPORT_RECORD["id"],
        "expected_official_export_sha256": OFFICIAL_EXPORT_SHA256,
        "expected_extra_sku_id": EXTRA_ACCESSORY_SKU_ID,
    }


def validate_request(payload: dict) -> bool:
    return isinstance(payload, dict) and payload == request_payload()


# Calculated after request_payload is defined, and frozen by its exact fields.
INVOCATION_SCOPE_SHA256 = v3._canonical_sha256(request_payload())


def _claim_invocation(db: Session) -> tuple[CampaignExecutionAttempt, bool]:
    existing = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == INVOCATION_OPERATION,
        CampaignExecutionAttempt.scope_sha256 == INVOCATION_SCOPE_SHA256,
    ).with_for_update()).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = CampaignExecutionAttempt(
        id=secrets.token_hex(12), plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY, operation=INVOCATION_OPERATION,
        scope_sha256=INVOCATION_SCOPE_SHA256, state="prepared",
        write_claimed=False, automatic_retry_allowed=False,
        result_summary={
            "recovery_id": RECOVERY_ID,
            "source_bundle_id": SOURCE_BUNDLE_ID,
            "official_export_sha256": OFFICIAL_EXPORT_SHA256,
            "execution_boundary": _boundary(),
        },
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.execute(select(CampaignExecutionAttempt).where(
            CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
            CampaignExecutionAttempt.operation == INVOCATION_OPERATION,
            CampaignExecutionAttempt.scope_sha256 == INVOCATION_SCOPE_SHA256,
        )).scalar_one()
        return row, False
    return row, True


def _finish_invocation(db: Session, invocation_id: str, *, state: str,
                       result: dict) -> None:
    row = db.get(CampaignExecutionAttempt, invocation_id)
    if row is None:
        raise RuntimeError("final_closeout_v4_invocation_missing")
    row.state = state
    row.last_step = str(result.get("step") or state)[:64]
    row.error_code = str(result.get("error") or "")[:128] or None
    row.automatic_retry_allowed = False
    row.result_summary = result
    db.commit()


def _plan_identity_error(plan: CampaignPlan | None) -> str | None:
    if plan is None:
        return "workflow_not_found"
    if (plan.id != PLAN_ID or plan.workflow_key != WORKFLOW_KEY
            or plan.status != EXPECTED_STATUS
            or plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or str(plan.qn_campaign_title or "").strip() != "超级立减"):
        return "final_closeout_v4_plan_identity_mismatch"
    return None


def _source_bundle_error(bundle: CampaignPreparationBundle | None) -> str | None:
    if bundle is None:
        return "final_closeout_v4_source_bundle_not_found"
    summary = bundle.summary if isinstance(bundle.summary, dict) else {}
    decisions = {
        str(row.get("taobao_item_id") or ""): str(row.get("state") or "")
        for row in (bundle.item_decisions or []) if isinstance(row, dict)
    }
    expected_decisions = {
        TARGET_ITEM_ID: "ready",
        **{item_id: "deferred_whole_item" for item_id in DEFERRED_ITEM_IDS},
    }
    checks = (
        (bundle.plan_id == PLAN_ID, "source_bundle_plan_mismatch"),
        (bundle.workflow_key == WORKFLOW_KEY, "source_bundle_workflow_mismatch"),
        (bundle.state == "ready_for_final_submission", "source_bundle_not_ready"),
        (not bundle.consumed_attempt_id, "source_bundle_already_consumed"),
        (bundle.source_sha256 == SOURCE_SHA256, "source_bundle_source_sha_mismatch"),
        (bundle.policy_sha256 == POLICY_SHA256, "source_bundle_policy_sha_mismatch"),
        (bundle.manifest_sha256 == SOURCE_MANIFEST_SHA256,
         "source_bundle_manifest_sha_mismatch"),
        (summary.get("exact_item_scope_sha256") == ITEM_SCOPE_SHA256,
         "source_bundle_item_scope_sha_mismatch"),
        (summary.get("global_blockers") == [], "source_bundle_global_blocked"),
        (decisions == expected_decisions, "source_bundle_decision_scope_mismatch"),
        (len(bundle.signup_rows or []) == EXPECTED_SOURCE_SIGNUP_ROWS,
         "source_bundle_signup_row_count_mismatch"),
        (len(bundle.discount_rows or []) == EXPECTED_SOURCE_DISCOUNT_ROWS,
         "source_bundle_discount_row_count_mismatch"),
        (v3._manifest_sha(
            bundle.identity, bundle.policy_sha256,
            list(bundle.signup_rows or []), list(bundle.discount_rows or []))
         == SOURCE_MANIFEST_SHA256, "source_bundle_manifest_content_mismatch"),
    )
    return next((error for ok, error in checks if not ok), None)


def _record_by_sku(records: list[dict]) -> dict[str, dict]:
    return {
        str(row.get("sku_id") or "").strip(): row for row in records
        if str(row.get("item_id") or "").strip() == TARGET_ITEM_ID
    }


def _fact_matches(record: dict | None, fact: dict, *, blank_code: bool) -> bool:
    if not record:
        return False
    try:
        stock = int(record.get("stock"))
    except (TypeError, ValueError):
        return False
    return bool(
        (not str(record.get("merchant_code") or "").strip() if blank_code
         else str(record.get("merchant_code") or "").strip()
         == str(fact["merchant_code"]))
        and str(record.get("sale_attr") or "").strip() == fact["sale_attr"]
        and Decimal(str(record.get("sku_price"))) == fact["sku_price"]
        and stock == fact["stock"]
    )


def _artifact_scope_error(records: list[dict], source_rows: list[dict]) -> dict | None:
    expected_pairs = {
        (str(row.get("taobao_item_id") or ""),
         str(row.get("taobao_sku_id") or "")) for row in source_rows
    }
    expected_pairs.add((TARGET_ITEM_ID, EXTRA_ACCESSORY_SKU_ID))
    actual_pairs = {
        (str(row.get("item_id") or ""), str(row.get("sku_id") or ""))
        for row in records if str(row.get("item_id") or "") == TARGET_ITEM_ID
    }
    by_sku = _record_by_sku(records)
    facts_ok = (
        _fact_matches(by_sku.get(PRIMARY_ACCESSORY_SKU_ID), {
            "merchant_code": ACCESSORY_SKU_CODE,
            "sale_attr": ACCESSORY_SPEC_BEECH,
            "sku_price": Decimal("290"), "stock": 100,
        }, blank_code=False)
        and _fact_matches(by_sku.get(EXTRA_ACCESSORY_SKU_ID),
                          EXTRA_ACCESSORY_FACT, blank_code=True)
        and all(_fact_matches(by_sku.get(sku_id), fact, blank_code=True)
                for sku_id, fact in PLACEHOLDER_FACTS.items())
    )
    if actual_pairs != expected_pairs or len(by_sku) != EXPECTED_SIGNUP_ROWS \
            or not facts_ok:
        return {
            "error": "final_closeout_v4_official_artifact_scope_mismatch",
            "expected_pairs": sorted(expected_pairs),
            "actual_pairs": sorted(actual_pairs),
            "critical_facts_match": facts_ok,
        }
    return None


def _identity_meaning(row: SkuIdentity) -> dict:
    return {key: getattr(row, key) for key in (
        "taobao_item_id", "taobao_sku_id", "merchant_code", "sku_spec",
        "sku_code", "product_code", "is_custom_placeholder",
    )}


def _correct_placeholder_identity(
        db: Session, *, sku_id: str, fact: dict,
        evidence_sha256: str) -> dict:
    row = db.execute(select(SkuIdentity).where(
        SkuIdentity.taobao_item_id == TARGET_ITEM_ID,
        SkuIdentity.taobao_sku_id == sku_id,
    ).with_for_update()).scalar_one_or_none()
    if row is None:
        raise ValueError(f"placeholder_identity_missing:{sku_id}")
    corrected = {
        "taobao_item_id": TARGET_ITEM_ID,
        "taobao_sku_id": sku_id,
        "merchant_code": fact["merchant_code"],
        "sku_spec": fact["sale_attr"],
        "sku_code": fact["merchant_code"],
        "product_code": "PPS26330110226",
        "is_custom_placeholder": True,
    }
    if _identity_meaning(row) == corrected:
        return {"sku_id": sku_id, "disposition": "already_correct"}
    expected = {
        "taobao_item_id": TARGET_ITEM_ID,
        "taobao_sku_id": sku_id,
        "merchant_code": "PPS26330110226",
        "sku_spec": fact["sale_attr"],
        "sku_code": None,
        "product_code": "PPS26330110226",
        "is_custom_placeholder": False,
    }
    repaired = sku_identity_service.authorize_canonical_correction(
        db, expected=expected, corrected=corrected,
        evidence_source="authorized_identity_correction:plan7_v4",
        evidence_sha256=evidence_sha256,
        authorization_ref=AUTHORIZATION_REF,
        daily_price=fact["sku_price"],
    )
    return {"sku_id": sku_id, **repaired}


def _repair_mapping(db: Session, *, evidence_sha256: str) -> dict:
    promo = db.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code == ACCESSORY_SKU_CODE
    ).with_for_update()).scalar_one_or_none()
    sku = db.execute(select(PricingSku).where(
        PricingSku.sku_code == ACCESSORY_SKU_CODE
    ).with_for_update()).scalar_one_or_none()
    if (promo is None or sku is None
            or str(promo.taobao_item_id or "") != TARGET_ITEM_ID
            or str(promo.taobao_sku_id or "") != PRIMARY_ACCESSORY_SKU_ID
            or Decimal(str(sku.list_price)) != Decimal("290")
            or Decimal(str(sku.daily_price)) != Decimal("217.50")
            or bool(sku.is_custom_placeholder)):
        raise ValueError("final_closeout_v4_accessory_erp_identity_drift")
    owners = []
    for candidate in db.execute(select(PricingSkuPromo)).scalars():
        ids = {str(candidate.taobao_sku_id or ""), *{
            str(value) for value in (candidate.alt_taobao_sku_ids or [])}}
        if EXTRA_ACCESSORY_SKU_ID in ids and candidate.sku_code != ACCESSORY_SKU_CODE:
            owners.append(candidate.sku_code)
    if owners:
        raise ValueError("final_closeout_v4_extra_sku_owned_elsewhere")
    current_alt = [str(value) for value in (promo.alt_taobao_sku_ids or [])]
    if any(value != EXTRA_ACCESSORY_SKU_ID for value in current_alt):
        raise ValueError("final_closeout_v4_accessory_alt_scope_drift")
    alt_added = EXTRA_ACCESSORY_SKU_ID not in current_alt
    if alt_added:
        promo.alt_taobao_sku_ids = [EXTRA_ACCESSORY_SKU_ID]
    corrections = [
        _correct_placeholder_identity(
            db, sku_id=sku_id, fact=fact, evidence_sha256=evidence_sha256)
        for sku_id, fact in PLACEHOLDER_FACTS.items()
    ]
    db.flush()
    return {
        "accessory_alt_added": alt_added,
        "sku_code": ACCESSORY_SKU_CODE,
        "primary_sku_id": PRIMARY_ACCESSORY_SKU_ID,
        "alt_sku_ids": [EXTRA_ACCESSORY_SKU_ID],
        "placeholder_identity_corrections": corrections,
    }


def _blank_fallbacks() -> dict[tuple[str, str], dict]:
    return {
        (TARGET_ITEM_ID, sku_id): fact
        for sku_id, fact in {
            **PLACEHOLDER_FACTS,
            EXTRA_ACCESSORY_SKU_ID: EXTRA_ACCESSORY_FACT,
        }.items()
    }


def _new_bundle_error(bundle: CampaignPreparationBundle | None) -> str | None:
    if bundle is None:
        return "final_closeout_v4_bundle_not_found"
    summary = bundle.summary if isinstance(bundle.summary, dict) else {}
    decisions = {
        str(row.get("taobao_item_id") or ""): str(row.get("state") or "")
        for row in (bundle.item_decisions or []) if isinstance(row, dict)
    }
    expected_decisions = {
        TARGET_ITEM_ID: "ready",
        **{item_id: "deferred_whole_item" for item_id in DEFERRED_ITEM_IDS},
    }
    expires_at = bundle.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    checks = (
        (bundle.plan_id == PLAN_ID, "bundle_plan_mismatch"),
        (bundle.workflow_key == WORKFLOW_KEY, "bundle_workflow_mismatch"),
        (bundle.state == "ready_for_final_submission", "bundle_not_ready"),
        (expires_at > datetime.now(timezone.utc), "bundle_expired"),
        (not bundle.consumed_attempt_id, "bundle_already_consumed"),
        (bundle.policy_sha256 == POLICY_SHA256, "bundle_policy_sha_mismatch"),
        (summary.get("exact_item_scope_sha256") == ITEM_SCOPE_SHA256,
         "bundle_item_scope_sha_mismatch"),
        (summary.get("global_blockers") == [], "bundle_global_blocked"),
        (decisions == expected_decisions, "bundle_decision_scope_mismatch"),
        (len(bundle.signup_rows or []) == EXPECTED_SIGNUP_ROWS,
         "bundle_signup_row_count_mismatch"),
        (len(bundle.discount_rows or []) == EXPECTED_DISCOUNT_ROWS,
         "bundle_discount_row_count_mismatch"),
        (v3._manifest_sha(
            bundle.identity, bundle.policy_sha256,
            list(bundle.signup_rows or []), list(bundle.discount_rows or []))
         == bundle.manifest_sha256, "bundle_manifest_content_mismatch"),
    )
    return next((error for ok, error in checks if not ok), None)


def validate_push_context(
        db: Session, plan: CampaignPlan, *, exact_item_scope: set[str] | None,
        policy_sha256: str, prepared_bundle_context: dict | None,
        official_identity: dict | None) -> tuple[bool, dict]:
    context = prepared_bundle_context or {}
    bundle = db.get(CampaignPreparationBundle, context.get("bundle_id"))
    error = _new_bundle_error(bundle)
    ok = bool(
        not error and plan.id == PLAN_ID and plan.workflow_key == WORKFLOW_KEY
        and plan.status == "resume_executing"
        and plan.campaign_type == "super_reduce"
        and exact_item_scope == {TARGET_ITEM_ID}
        and policy_sha256 == POLICY_SHA256
        and context == {
            "bundle_id": bundle.id,
            "source_sha256": bundle.source_sha256,
            "policy_sha256": bundle.policy_sha256,
            "manifest_sha256": bundle.manifest_sha256,
            "item_scope_sha256": ITEM_SCOPE_SHA256,
        }
        and isinstance(official_identity, dict)
        and official_identity.get("ok")
        and official_identity.get("checked_items") == 1
        and official_identity.get("checked_skus") == EXPECTED_SIGNUP_ROWS
        and official_identity.get("official_skus") == EXPECTED_SIGNUP_ROWS
        and (official_identity.get("artifact") or {}).get("sha256")
        == OFFICIAL_EXPORT_SHA256
    )
    return ok, {"error": error, "bundle_id": context.get("bundle_id")}


def execute_plan7_final_closeout_v4(db: Session, payload: dict) -> dict:
    if not validate_request(payload):
        return _fail("final_closeout_v4_request_not_allowed")
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    error = _plan_identity_error(plan)
    if error:
        return _fail(error)
    source_bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.id == SOURCE_BUNDLE_ID,
    ).with_for_update()).scalar_one_or_none()
    error = _source_bundle_error(source_bundle)
    if error:
        return _fail(error)
    source_rows = list(source_bundle.signup_rows or [])
    invocation, created = _claim_invocation(db)
    if not created:
        return _fail(
            "final_closeout_v4_already_invoked_no_retry",
            invocation_id=invocation.id, invocation_state=invocation.state)

    exported = web_agent_service.export_product_prices(
        db, timeout_s=420, recovery_record=dict(OFFICIAL_EXPORT_RECORD),
        item_ids=None)
    if not exported.get("ok"):
        failure = _fail(
            "final_closeout_v4_official_export_recovery_failed",
            platform_read=True, detail=exported)
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure
    content = exported.get("xlsx_bytes") or b""
    artifact_sha256 = hashlib.sha256(content).hexdigest()
    returned_record = exported.get("record") or {}
    record_matches = all(
        str(returned_record.get(key) if returned_record.get(key) is not None else "")
        == str(value)
        for key, value in OFFICIAL_EXPORT_RECORD.items()
        if key != "expected_sha256"
    )
    if (artifact_sha256 != OFFICIAL_EXPORT_SHA256
            or exported.get("export_created") is not False
            or not record_matches):
        failure = _fail(
            "final_closeout_v4_official_export_identity_mismatch",
            platform_read=True, actual_sha256=artifact_sha256,
            export_created=exported.get("export_created"),
            record=exported.get("record"))
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure
    records = campaign_recon_service.parse_product_batch_export(content)
    scope_error = _artifact_scope_error(records, source_rows)
    if scope_error:
        failure = _fail(
            scope_error.pop("error"), platform_read=True, **scope_error)
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure
    artifact = {
        "filename": exported.get("filename"), "size": len(content),
        "sha256": artifact_sha256, "job_id": exported.get("job_id"),
        "record": exported.get("record"),
        "download_mode": exported.get("download_mode"),
        "export_created": False,
    }

    try:
        plan = db.execute(select(CampaignPlan).where(
            CampaignPlan.id == PLAN_ID).with_for_update()).scalar_one()
        if _plan_identity_error(plan):
            raise ValueError("final_closeout_v4_plan_cas_changed")
        source_bundle = db.execute(select(CampaignPreparationBundle).where(
            CampaignPreparationBundle.id == SOURCE_BUNDLE_ID
        ).with_for_update()).scalar_one()
        source_error = _source_bundle_error(source_bundle)
        if source_error:
            raise ValueError(source_error)
        mapping = _repair_mapping(db, evidence_sha256=artifact_sha256)
        signup_rows, _stats = campaign_service.build_signup_rows(db, plan)
        signup_rows = [row for row in signup_rows
                       if str(row.get("taobao_item_id") or "") == TARGET_ITEM_ID]
        discount_rows, _discount_stats = campaign_service.build_discount_rows(db, plan)
        discount_rows = [row for row in discount_rows
                         if str(row.get("taobao_item_id") or "") == TARGET_ITEM_ID]
        expected_pairs = {
            (str(row.get("taobao_item_id") or ""),
             str(row.get("taobao_sku_id") or "")) for row in signup_rows
        }
        if (len(signup_rows) != EXPECTED_SIGNUP_ROWS
                or len(discount_rows) != EXPECTED_DISCOUNT_ROWS
                or (TARGET_ITEM_ID, EXTRA_ACCESSORY_SKU_ID) not in expected_pairs):
            raise ValueError("final_closeout_v4_recompiled_row_scope_mismatch")
        official_identity = (
            campaign_service._validate_official_product_sku_identity_records(
                db, signup_rows, records, plan=plan, artifact=artifact,
                blank_merchant_fallbacks=_blank_fallbacks()))
        if (not official_identity.get("ok")
                or official_identity.get("checked_items") != 1
                or official_identity.get("checked_skus") != EXPECTED_SIGNUP_ROWS
                or official_identity.get("official_skus") != EXPECTED_SIGNUP_ROWS):
            raise ValueError("final_closeout_v4_official_sku_identity_failed")
        compiled = campaign_preparation_service.compile_bundle(
            db, workflow_key=WORKFLOW_KEY, expected_plan_id=PLAN_ID,
            expected_status=EXPECTED_STATUS, refresh_evidence=False,
            exact_item_scope={TARGET_ITEM_ID, *DEFERRED_ITEM_IDS},
            prepared_by="service:campaign-plan7-final-closeout-v4")
    except Exception as exc:  # noqa: BLE001 - no platform write exists yet
        db.rollback()
        failure = _fail(
            "final_closeout_v4_prewrite_repair_failed", platform_read=True,
            detail=f"{type(exc).__name__}: {exc}")
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure
    if not compiled.get("ok") or not compiled.get("ready_for_final_submission"):
        failure = _fail(
            "final_closeout_v4_bundle_compile_blocked", platform_read=True,
            compiled=compiled)
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure
    bundle = db.get(CampaignPreparationBundle, compiled["bundle_id"])
    error = _new_bundle_error(bundle)
    if error:
        failure = _fail(error, platform_read=True, compiled=compiled)
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID).with_for_update()).scalar_one()
    bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.id == bundle.id
    ).with_for_update()).scalar_one()
    error = _plan_identity_error(plan) or _new_bundle_error(bundle)
    if error:
        failure = _fail(error, platform_read=True)
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure
    policy = campaign_policy_service.require_policy()
    execution_scope_sha = campaign_execution_service.scope_sha256(
        identity=campaign_service.campaign_identity(plan),
        rows=list(bundle.signup_rows or []),
        policy_sha256=str(policy.get("_sha256") or ""))
    attempt, created = campaign_execution_service.ensure_attempt(
        db, plan=plan, scope_sha256_value=execution_scope_sha,
        result_summary={
            "prepared_bundle_id": bundle.id,
            "source_bundle_id": SOURCE_BUNDLE_ID,
            "official_export_sha256": OFFICIAL_EXPORT_SHA256,
            "invocation_id": invocation.id,
            "mapping_repair": mapping,
            "signup_rows": EXPECTED_SIGNUP_ROWS,
            "discount_rows_verified": EXPECTED_DISCOUNT_ROWS,
            "recovery_id": RECOVERY_ID,
            "official_product_sku_identity": official_identity,
        })
    if not created:
        failure = _fail(
            "final_closeout_v4_existing_signup_attempt_blocks_execution",
            platform_read=True, attempt_id=attempt.id,
            attempt_state=attempt.state, write_claimed=attempt.write_claimed)
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure

    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID).with_for_update()).scalar_one()
    bundle = db.execute(select(CampaignPreparationBundle).where(
        CampaignPreparationBundle.id == bundle.id
    ).with_for_update()).scalar_one()
    if _plan_identity_error(plan) or _new_bundle_error(bundle):
        failure = _fail(
            "final_closeout_v4_cas_changed_before_claim", platform_read=True,
            plan_status=plan.status, bundle_id=bundle.id)
        _finish_invocation(
            db, invocation.id, state="blocked_prewrite", result=failure)
        return failure
    bundle.consumed_at = datetime.now(timezone.utc)
    bundle.consumed_attempt_id = attempt.id
    plan.status = "resume_executing"
    db.commit()
    context = {
        "bundle_id": bundle.id,
        "source_sha256": bundle.source_sha256,
        "policy_sha256": bundle.policy_sha256,
        "manifest_sha256": bundle.manifest_sha256,
        "item_scope_sha256": ITEM_SCOPE_SHA256,
    }
    try:
        result = campaign_service.push_signup(
            db, plan, execution_source=EXECUTION_SOURCE,
            reuse_fresh_plan_evidence=True,
            exact_item_scope={TARGET_ITEM_ID},
            allow_terminal_no_sales_fallback=False,
            prepared_official_product_identity=official_identity,
            prepared_bundle_context=context)
    except Exception as exc:  # noqa: BLE001 - claimed outcome must fail closed
        db.rollback()
        plan = db.get(CampaignPlan, PLAN_ID)
        if plan is not None:
            plan.status = "alarmed"
            db.commit()
        attempt = db.get(CampaignExecutionAttempt, attempt.id)
        if attempt is not None and attempt.write_claimed \
                and attempt.state == "write_claimed":
            campaign_execution_service.record_platform_terminal(
                db, attempt, state="unknown_no_retry",
                platform_write_observed=None,
                step="plan7_final_closeout_v4_exception",
                error_code=type(exc).__name__,
                result_summary={"bundle_id": bundle.id})
        failure = {
            "ok": False,
            "error": "final_closeout_v4_unknown_outcome_no_retry",
            "attempt_id": attempt.id if attempt else None,
            "execution_boundary": _boundary(
                platform_read=True, platform_write=None),
        }
        _finish_invocation(
            db, invocation.id, state="unknown_no_retry", result=failure)
        return failure
    if not result.get("ok"):
        failure = {
            "ok": False,
            "error": result.get("error") or "final_closeout_v4_failed_no_retry",
            "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
            "bundle_id": bundle.id, "attempt_id": attempt.id,
            "result": result,
            "execution_boundary": _boundary(
                platform_read=True,
                platform_write=(True if result.get("submitted") else None)),
        }
        _finish_invocation(
            db, invocation.id, state="failed_no_retry", result=failure)
        return failure
    plan = db.get(CampaignPlan, PLAN_ID)
    plan.status = "reconciled"
    marker = (
        f"final_closeout_v4_bundle={bundle.id}; "
        f"final_closeout_v4_ready_item={TARGET_ITEM_ID}; "
        f"final_closeout_v4_scope_sha256={ITEM_SCOPE_SHA256}"
    )
    if marker not in str(plan.remark or ""):
        plan.remark = f"{plan.remark or ''}; {marker}".strip("; ")
    db.commit()
    success = {
        "ok": True, "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
        "plan_status": plan.status, "bundle_id": bundle.id,
        "source_bundle_id": SOURCE_BUNDLE_ID,
        "attempt_id": attempt.id, "scope_sha256": execution_scope_sha,
        "submitted_item_ids": [TARGET_ITEM_ID],
        "deferred_item_ids": sorted(DEFERRED_ITEM_IDS),
        "preserved_active_item_ids": sorted(PRESERVED_ACTIVE_ITEM_IDS),
        "exempt_item_ids": sorted(EXEMPT_ITEM_IDS),
        "mapping_repair": mapping, "result": result,
        "execution_boundary": _boundary(
            platform_read=True, platform_write=True),
    }
    _finish_invocation(db, invocation.id, state="completed", result=success)
    return success

"""One-shot in-place completion for the six existing plan-8 drafts.

V2 treated SKUs hidden from the candidate picker as unavailable.  V3 binds the
official six draft records instead: supplement eight single-item discounts,
add four mapped SKUs to each of two drafts, publish all six drafts, then require
an official readback proving 6 items / 78 SKUs / 18 custom SKUs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_execution_service,
    campaign_policy_service,
    campaign_service,
    web_agent_service,
)


WORKFLOW_KEY = "campaign:super88:49462:49469"
PLAN_ID = 8
EXPECTED_STATUS = "alarmed"
RECOVERY_VERSION = 3
OPERATION = "plan8_final_recovery_v3"
EXECUTION_SOURCE = "campaign_super88_plan8_final_recovery_v3"
EXPECTED_POLICY_SHA256 = (
    "0209e67546c2e20be904a54d16402a32de02d91129bc1cceeda34b6e6fa4483f"
)
EXPECTED_TARGET_SCOPE_SHA256 = (
    "b239dc515b0f2442257e90fe30a1cda95e29f6ffd2ea123d6c53f6fd6a4feb1d"
)
EXPECTED_EXPORT_SHA256 = (
    "e351da1486bddbd8f4a79f0fec658802dbdb335ad4c7cbbd5e4287383844129"
)
EXPECTED_CANDIDATE_SHA256 = (
    "91cb317762b45940d41193e247dbd8d1d5d54839093caf373579098c4a66def8"
)

IDENTITY = {
    "campaign_title": "26年淘宝9月超级88",
    "campaign_phase": "超级88现货",
    "campaign_id": "49462",
    "united_activity_id": "49469",
    "sign_record_id": "3527841611",
    "campaign_start": "2026-09-06 20:00:00",
    "campaign_end": "2026-09-13 23:59:59",
    "official_rate": "12%",
    "platform_activity_mode": "fixed_window",
}

DRAFT_RECORDS = {
    "1036279566778": {
        "record_id": "10031117357515", "current_sku_count": 16,
        "final_sku_count": 20,
        "add_sku_ids": (
            "6234601898881", "6234601898883", "6234601898885",
            "6234601898887",
        ),
    },
    "1036312802226": {
        "record_id": "10031113337291", "current_sku_count": 5,
        "final_sku_count": 5, "add_sku_ids": (),
    },
    "1074244132390": {
        "record_id": "10031118975435", "current_sku_count": 16,
        "final_sku_count": 20,
        "add_sku_ids": (
            "6287431318354", "6287431318356", "6287431318358",
            "6287431318360",
        ),
    },
    "837902729785": {
        "record_id": "10031117105611", "current_sku_count": 14,
        "final_sku_count": 14, "add_sku_ids": (),
    },
    "841201084787": {
        "record_id": "10031118516683", "current_sku_count": 11,
        "final_sku_count": 11, "add_sku_ids": (),
    },
    "917179577721": {
        "record_id": "10031119608267", "current_sku_count": 8,
        "final_sku_count": 8, "add_sku_ids": (),
    },
}
PROTECTED_RECORDS = {
    "1001358847694": {"record_id": "10031089539531", "sku_count": 5},
    "805268708396": {"record_id": "10031166890443", "sku_count": 7},
    "863525290377": {"record_id": "10031092556235", "sku_count": 1},
}
ZERO_SALES_EXCLUDED_ITEM_ID = "793202812082"
WAREHOUSE_EXCLUDED_ITEM_ID = "1038725569412"
TARGET_ITEM_IDS = set(DRAFT_RECORDS)
ADD_SKU_IDS = {
    sku_id for record in DRAFT_RECORDS.values()
    for sku_id in record["add_sku_ids"]
}
ADD_PAIRS = {
    (item_id, sku_id) for item_id, record in DRAFT_RECORDS.items()
    for sku_id in record["add_sku_ids"]
}
EXPECTED_DISCOUNT_DEDUCTS = {
    ("1036279566778", "6234601898881"): "1426.94",
    ("1036279566778", "6234601898883"): "1454.92",
    ("1036279566778", "6234601898885"): "1513.77",
    ("1036279566778", "6234601898887"): "1576.32",
    ("1074244132390", "6287431318354"): "1452.12",
    ("1074244132390", "6287431318356"): "1492.29",
    ("1074244132390", "6287431318358"): "1538.04",
    ("1074244132390", "6287431318360"): "1613.68",
}
EXPECTED_TARGET_ROW_COUNT = 78
EXPECTED_TARGET_CUSTOM_ROW_COUNT = 18

PREREQUISITE_ATTEMPTS = {
    "14ddfc8e428148b66f61c7aa": (
        "plan8_discount_and_signup", "failed_no_retry", True),
    "a3d7dfd9d65d7a5e62ad4afd": ("signup", "failed_no_retry", True),
    "26b67a144f9448d65ef56c66": (
        "plan8_signup_recovery", "failed_no_retry", True),
    "05a12142148dd04d25a88d48": (
        "plan8_sku_mapping_repair", "completed", True),
}


def _boundary(*, platform_write: bool = False) -> dict:
    return {
        "plan_scoped_only": True,
        "platform_read": True,
        "platform_write": platform_write,
        "erp_daily_price_change": False,
        "sku_rotation": False,
        "withdraw_pause_remove": False,
        "warehouse_item_write": False,
        "candidate_picker_used_for_draft_skus": False,
        "old_53_discount_rows_replayed": False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, **detail) -> dict:
    return {"ok": False, "error": error, **detail,
            "execution_boundary": _boundary()}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()


def _money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"


def _identity_allowed(plan: CampaignPlan) -> tuple[bool, dict]:
    identity = campaign_service.campaign_identity(plan)
    expected_plan_identity = {
        key: value for key, value in IDENTITY.items() if key != "campaign_phase"
    }
    return bool(identity.get("ok") and str(plan.name or "") == IDENTITY["campaign_phase"] and all(
        str(identity.get(key) or "") == value
        for key, value in expected_plan_identity.items()
    )), identity


def _validate_prerequisites(db: Session) -> tuple[bool, list[dict]]:
    detail = []
    ok = True
    for attempt_id, expected in PREREQUISITE_ATTEMPTS.items():
        row = db.get(CampaignExecutionAttempt, attempt_id)
        actual = {
            "attempt_id": attempt_id,
            "operation": getattr(row, "operation", None),
            "state": getattr(row, "state", None),
            "write_claimed": getattr(row, "write_claimed", None),
        }
        detail.append(actual)
        if row is None or (
            actual["operation"], actual["state"], actual["write_claimed"]
        ) != expected:
            ok = False
    return ok, detail


def _target_rows(db: Session, plan: CampaignPlan, identity: dict,
                 policy_sha: str) -> tuple[list[dict], dict | None]:
    all_rows, stats = campaign_service.build_signup_rows(db, plan)
    rows = [row for row in all_rows
            if str(row.get("taobao_item_id") or "") in TARGET_ITEM_IDS]
    item_ids = {str(row.get("taobao_item_id") or "") for row in rows}
    sku_ids = {str(row.get("taobao_sku_id") or "") for row in rows}
    custom_ids = {str(row.get("taobao_sku_id") or "") for row in rows
                  if row.get("is_placeholder") is True}
    actual_scope = campaign_execution_service.scope_sha256(
        identity=identity, rows=rows, policy_sha256=policy_sha)
    if not (
        len(rows) == EXPECTED_TARGET_ROW_COUNT
        and len(sku_ids) == EXPECTED_TARGET_ROW_COUNT
        and item_ids == TARGET_ITEM_IDS
        and len(custom_ids) == EXPECTED_TARGET_CUSTOM_ROW_COUNT
        and actual_scope == EXPECTED_TARGET_SCOPE_SHA256
        and ADD_SKU_IDS <= sku_ids
    ):
        return rows, {
            "error": "plan8_final_v3_signup_scope_drift",
            "row_count": len(rows),
            "sku_count": len(sku_ids),
            "item_ids": sorted(item_ids),
            "custom_sku_count": len(custom_ids),
            "missing_add_sku_ids": sorted(ADD_SKU_IDS - sku_ids),
            "actual_scope_sha256": actual_scope,
            "stats": stats,
        }
    return rows, None


def _discount_scope(db: Session, plan: CampaignPlan) -> tuple[list[dict], dict | None]:
    all_rows, stats = campaign_service.build_discount_rows(db, plan)
    rows = [row for row in all_rows if (
        str(row.get("taobao_item_id") or ""),
        str(row.get("taobao_sku_id") or ""),
    ) in ADD_PAIRS]
    pairs = {(str(row.get("taobao_item_id") or ""),
              str(row.get("taobao_sku_id") or "")) for row in rows}
    if len(rows) != 8 or pairs != ADD_PAIRS:
        return rows, {
            "error": "plan8_final_v3_discount_scope_drift",
            "row_count": len(rows), "pairs": sorted(pairs), "stats": stats,
        }
    normalized = []
    for row in rows:
        item_id = str(row.get("taobao_item_id") or "")
        sku_id = str(row.get("taobao_sku_id") or "")
        actual_deduct = f"{Decimal(str(row.get('deduct'))).quantize(Decimal('0.01')):.2f}"
        expected_deduct = EXPECTED_DISCOUNT_DEDUCTS[(item_id, sku_id)]
        if actual_deduct != expected_deduct:
            return rows, {
                "error": "plan8_final_v3_discount_amount_drift",
                "item_id": item_id, "sku_id": sku_id,
                "expected_deduct": expected_deduct,
                "actual_deduct": actual_deduct,
            }
        normalized.append({
            "item_id": item_id, "sku_id": sku_id,
            "expected_deduct": expected_deduct,
        })
    return sorted(normalized, key=lambda row: (row["item_id"], row["sku_id"])), None


def _fixed_manifest(target_rows: list[dict], discount_rows: list[dict],
                    policy_sha: str) -> dict:
    by_sku = {str(row.get("taobao_sku_id") or ""): row for row in target_rows}
    records = []
    for item_id in sorted(DRAFT_RECORDS):
        spec = DRAFT_RECORDS[item_id]
        item_rows = [row for row in target_rows
                     if str(row.get("taobao_item_id") or "") == item_id]
        records.append({
            "item_id": item_id,
            "record_id": spec["record_id"],
            "expected_current_status": "草稿",
            "expected_current_sku_count": spec["current_sku_count"],
            "expected_final_status": "已发布",
            "expected_final_sku_count": spec["final_sku_count"],
            "add_sku_ids": list(spec["add_sku_ids"]),
            "add_rows": [{
                "item_id": item_id,
                "sku_id": sku_id,
                "signup_price": _money(by_sku[sku_id].get("price")),
                "is_custom": by_sku[sku_id].get("is_placeholder") is True,
            } for sku_id in spec["add_sku_ids"]],
            "final_sku_ids": sorted(
                sku_id for sku_id, row in by_sku.items()
                if str(row.get("taobao_item_id") or "") == item_id),
            "expected_sku_rows": sorted([{
                "sku_id": str(row.get("taobao_sku_id") or ""),
                "signup_price": _money(row.get("price")),
                "is_custom": row.get("is_placeholder") is True,
            } for row in item_rows], key=lambda row: row["sku_id"]),
        })
    custom_sku_ids = sorted(
        str(row.get("taobao_sku_id") or "") for row in target_rows
        if row.get("is_placeholder") is True)
    return {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "recovery_version": RECOVERY_VERSION,
        "identity": dict(IDENTITY),
        "source_evidence": {
            "official_export_sha256": EXPECTED_EXPORT_SHA256,
            "candidate_export_sha256": EXPECTED_CANDIDATE_SHA256,
        },
        "draft_records": records,
        "protected_records": [
            {"item_id": item_id, **PROTECTED_RECORDS[item_id]}
            for item_id in sorted(PROTECTED_RECORDS)
        ],
        "discount_rows": discount_rows,
        "legacy_discount_baseline": {"expected_row_count": 53},
        "excluded_scope": {
            "zero_sales_item_ids": [ZERO_SALES_EXCLUDED_ITEM_ID],
            "warehouse_item_ids": [WAREHOUSE_EXCLUDED_ITEM_ID],
        },
        "final_scope": {
            "item_ids": sorted(TARGET_ITEM_IDS),
            "sku_ids": sorted(by_sku),
            "custom_sku_ids": custom_sku_ids,
            "item_count": 6,
            "sku_count": EXPECTED_TARGET_ROW_COUNT,
            "custom_sku_count": EXPECTED_TARGET_CUSTOM_ROW_COUNT,
            "scope_sha256": EXPECTED_TARGET_SCOPE_SHA256,
            "policy_sha256": policy_sha,
        },
        "execution_order": [
            "supplement_8_single_item_discounts",
            "add_4_plus_4_skus_to_two_bound_drafts",
            "publish_6_bound_drafts",
            "official_readback",
        ],
    }


def _record_map(result: dict) -> dict[str, dict]:
    return {str(row.get("item_id") or ""): row
            for row in result.get("draft_records") or []
            if isinstance(row, dict)}


def _valid_sha(value) -> bool:
    import re
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "")))


def _sku_price_map(rows: list[dict]) -> dict[str, str] | None:
    result = {}
    try:
        for row in rows:
            sku_id = str(row.get("sku_id") or "")
            if not sku_id or sku_id in result:
                return None
            result[sku_id] = _money(row.get("signup_price"))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return result


def _discount_value_map(rows: list[dict]) -> dict[tuple[str, str], str] | None:
    result = {}
    try:
        for row in rows:
            key = (str(row.get("item_id") or ""),
                   str(row.get("sku_id") or ""))
            if not all(key) or key in result:
                return None
            result[key] = _money(row.get("expected_deduct"))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return result


def validate_inspection(result: dict, manifest: dict,
                        manifest_sha256: str) -> tuple[bool, dict]:
    records = _record_map(result)
    expected_records = {
        str(row["item_id"]): row for row in manifest["draft_records"]
    }
    record_detail = {}
    records_ok = set(records) == TARGET_ITEM_IDS
    for item_id, spec in DRAFT_RECORDS.items():
        row = records.get(item_id) or {}
        expected_record = expected_records[item_id]
        sku_ids = {str(value) for value in row.get("sku_ids") or []}
        expected_current_skus = (
            set(expected_record["final_sku_ids"])
            - set(expected_record["add_sku_ids"])
        )
        actual_price_map = _sku_price_map(row.get("sku_rows") or [])
        expected_price_map = {
            value["sku_id"]: value["signup_price"]
            for value in expected_record["expected_sku_rows"]
            if value["sku_id"] in expected_current_skus
        }
        row_ok = (
            str(row.get("record_id") or "") == spec["record_id"]
            and str(row.get("status") or "") == "草稿"
            and row.get("sku_count") == spec["current_sku_count"]
            and sku_ids == expected_current_skus
            and actual_price_map == expected_price_map
            and _valid_sha(row.get("before_hash"))
            and row.get("before_hash") == _hash(row.get("snapshot"))
        )
        record_detail[item_id] = {"ok": row_ok, **row}
        records_ok = records_ok and row_ok
    discount_rows = result.get("discount_rows") or []
    discount_pairs = {(str(row.get("item_id") or ""),
                       str(row.get("sku_id") or "")) for row in discount_rows}
    discounts_ok = (
        len(discount_rows) == 8 and discount_pairs == ADD_PAIRS
        and _discount_value_map(discount_rows) == EXPECTED_DISCOUNT_DEDUCTS
        and all(str(row.get("state") or "") in {"missing", "correct"}
                for row in discount_rows)
        and all(str(row.get("activity_id") or "").isdigit()
                for row in discount_rows)
        and all(len({str(row.get("activity_id")) for row in discount_rows
                     if str(row.get("item_id")) == item_id}) == 1
                for item_id in {pair[0] for pair in ADD_PAIRS})
    )
    protected = {str(row.get("item_id") or ""): row
                 for row in result.get("protected_records") or []}
    protected_ok = set(protected) == set(PROTECTED_RECORDS)
    for item_id, spec in PROTECTED_RECORDS.items():
        row = protected.get(item_id) or {}
        protected_ok = protected_ok and (
            str(row.get("record_id") or "") == spec["record_id"]
            and str(row.get("status") or "") in {"已发布", "生效", "活动中"}
            and row.get("sku_count") == spec["sku_count"]
            and len({str(value) for value in row.get("sku_ids") or []})
            == spec["sku_count"]
            and _valid_sha(row.get("before_hash"))
            and row.get("before_hash") == _hash(row.get("snapshot"))
        )
    legacy = result.get("legacy_discount_baseline") or {}
    legacy_ok = (
        legacy.get("row_count") == 53
        and len(legacy.get("rows") or []) == 53
        and _valid_sha(legacy.get("sha256"))
        and legacy.get("sha256") == _hash(legacy.get("rows"))
    )
    all_record_ids = {str(value) for value in result.get("all_record_ids") or []}
    expected_record_ids = {
        row["record_id"] for row in manifest["draft_records"]
    } | {row["record_id"] for row in manifest["protected_records"]}
    exclusions_ok = (
        set(result.get("excluded_item_ids") or [])
        == {ZERO_SALES_EXCLUDED_ITEM_ID, WAREHOUSE_EXCLUDED_ITEM_ID}
        and all_record_ids == expected_record_ids
    )
    reservation_token = str(result.get("reservation_token") or "")
    ok = bool(
        result.get("ok") is True
        and not result.get("busy")
        and result.get("platform_write") is False
        and result.get("scope_sha256") == manifest_sha256
        and result.get("identity") == IDENTITY
        and records_ok and discounts_ok and protected_ok and legacy_ok
        and exclusions_ok and len(reservation_token) >= 16
        and result.get("reservation_active") is True
        and _valid_sha(result.get("artifact_sha256"))
    )
    return ok, {
        "busy": bool(result.get("busy")),
        "step": result.get("step"),
        "records": record_detail,
        "discount_rows": discount_rows,
        "protected_records": list(protected.values()),
        "legacy_discount_baseline": legacy,
        "all_record_ids": sorted(all_record_ids),
        "excluded_item_ids": sorted(result.get("excluded_item_ids") or []),
        "artifact_sha256": result.get("artifact_sha256"),
        "reservation_token_sha256": _hash(reservation_token),
        "reservation_active": result.get("reservation_active"),
        "scope_sha256": result.get("scope_sha256"),
        "web_agent_job_id": result.get("web_agent_job_id"),
    }


def enrich_manifest_with_inspection(manifest: dict, detail: dict) -> dict:
    baseline = {
        "official_artifact_sha256": detail["artifact_sha256"],
        "draft_record_before_hashes": {
            str(row["item_id"]): str(row["before_hash"])
            for row in detail["records"].values()
        },
        "protected_record_before_hashes": {
            str(row["item_id"]): str(row["before_hash"])
            for row in detail["protected_records"]
        },
        "legacy_discount_sha256": str(
            detail["legacy_discount_baseline"]["sha256"]),
        "legacy_discount_row_count": 53,
        "new_discount_before_rows": detail["discount_rows"],
        "new_discount_before_sha256": _hash(detail["discount_rows"]),
        "all_record_ids": detail["all_record_ids"],
        "excluded_item_ids": detail["excluded_item_ids"],
        "reservation_token_sha256": detail["reservation_token_sha256"],
    }
    return {**manifest, "inspection_baseline": baseline}


def validate_commit(result: dict, manifest: dict,
                    manifest_sha256: str) -> tuple[bool, dict]:
    baseline = manifest["inspection_baseline"]
    detail = {
        "step": result.get("step"),
        "platform_write": result.get("platform_write"),
        "scope_sha256": result.get("scope_sha256"),
        "inspection_baseline": result.get("inspection_baseline"),
        "discount_rows_written": result.get("discount_rows_written"),
        "draft_records_updated": result.get("draft_records_updated"),
        "draft_records_published": result.get("draft_records_published"),
        "reservation_consumed": result.get("reservation_consumed"),
        "web_agent_job_id": result.get("web_agent_job_id"),
    }
    ok = bool(
        result.get("ok") is True
        and result.get("platform_write") is True
        and result.get("scope_sha256") == manifest_sha256
        and result.get("inspection_baseline") == baseline
        and result.get("discount_rows_written") == 8
        and result.get("draft_records_updated") == 2
        and result.get("draft_records_published") == 6
        and result.get("reservation_consumed") is True
    )
    return ok, detail


def validate_readback(result: dict, manifest: dict,
                      manifest_sha256: str) -> tuple[bool, dict]:
    records = _record_map(result)
    final_skus = set()
    records_ok = set(records) == TARGET_ITEM_IDS
    for item_id, spec in DRAFT_RECORDS.items():
        row = records.get(item_id) or {}
        sku_ids = {str(value) for value in row.get("sku_ids") or []}
        expected_record = next(
            record for record in manifest["draft_records"]
            if record["item_id"] == item_id)
        expected_item_skus = set(expected_record["final_sku_ids"])
        expected_price_map = {
            value["sku_id"]: value["signup_price"]
            for value in expected_record["expected_sku_rows"]
        }
        actual_price_map = _sku_price_map(row.get("sku_rows") or [])
        final_skus.update(sku_ids)
        records_ok = records_ok and (
            str(row.get("record_id") or "") == spec["record_id"]
            and str(row.get("status") or "") in {"已发布", "生效", "活动中"}
            and row.get("sku_count") == spec["final_sku_count"]
            and len(sku_ids) == spec["final_sku_count"]
            and sku_ids == expected_item_skus
            and actual_price_map == expected_price_map
            and set(spec["add_sku_ids"]) <= sku_ids
        )
    expected_skus = set(manifest["final_scope"]["sku_ids"])
    custom_skus = {str(value) for value in result.get("custom_sku_ids") or []}
    expected_custom = set(manifest["final_scope"]["custom_sku_ids"])
    discount_rows = result.get("discount_rows") or []
    discount_pairs = {(str(row.get("item_id") or ""),
                       str(row.get("sku_id") or "")) for row in discount_rows}
    discounts_ok = (
        len(discount_rows) == 8 and discount_pairs == ADD_PAIRS
        and _discount_value_map(discount_rows) == EXPECTED_DISCOUNT_DEDUCTS
        and all(str(row.get("state") or "") in {"active", "correct"}
                for row in discount_rows)
    )
    protected = {str(row.get("item_id") or ""): row
                 for row in result.get("protected_records") or []}
    protected_ok = set(protected) == set(PROTECTED_RECORDS)
    for item_id, spec in PROTECTED_RECORDS.items():
        row = protected.get(item_id) or {}
        protected_ok = protected_ok and (
            str(row.get("record_id") or "") == spec["record_id"]
            and str(row.get("status") or "") in {"已发布", "生效", "活动中"}
            and row.get("sku_count") == spec["sku_count"]
            and str(row.get("after_hash") or "") == manifest[
                "inspection_baseline"]["protected_record_before_hashes"][item_id]
            and row.get("after_hash") == _hash(row.get("snapshot"))
        )
    legacy = result.get("legacy_discount_baseline") or {}
    legacy_ok = (
        legacy.get("row_count") == 53
        and len(legacy.get("rows") or []) == 53
        and legacy.get("sha256")
        == manifest["inspection_baseline"]["legacy_discount_sha256"]
        and legacy.get("sha256") == _hash(legacy.get("rows"))
    )
    all_record_ids = {str(value) for value in result.get("all_record_ids") or []}
    expected_record_ids = set(manifest["inspection_baseline"]["all_record_ids"])
    exclusions_ok = (
        all_record_ids == expected_record_ids
        and set(result.get("excluded_item_ids") or [])
        == {ZERO_SALES_EXCLUDED_ITEM_ID, WAREHOUSE_EXCLUDED_ITEM_ID}
    )
    ok = bool(
        result.get("ok") is True
        and result.get("platform_write") is False
        and result.get("scope_sha256") == manifest_sha256
        and result.get("identity") == IDENTITY
        and records_ok
        and final_skus == expected_skus
        and custom_skus == expected_custom
        and len(custom_skus) == EXPECTED_TARGET_CUSTOM_ROW_COUNT
        and discounts_ok and protected_ok and legacy_ok and exclusions_ok
        and result.get("inspection_baseline") == manifest["inspection_baseline"]
        and _valid_sha(result.get("artifact_sha256"))
    )
    return ok, {
        "record_count": len(records),
        "sku_count": len(final_skus),
        "custom_sku_count": len(custom_skus),
        "missing_sku_ids": sorted(expected_skus - final_skus),
        "unexpected_sku_ids": sorted(final_skus - expected_skus),
        "discount_rows": discount_rows,
        "protected_records": list(protected.values()),
        "legacy_discount_baseline": legacy,
        "all_record_ids": sorted(all_record_ids),
        "excluded_item_ids": sorted(result.get("excluded_item_ids") or []),
        "artifact_sha256": result.get("artifact_sha256"),
        "web_agent_job_id": result.get("web_agent_job_id"),
    }


def _attempts(db: Session) -> list[CampaignExecutionAttempt]:
    return list(db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
    )).scalars())


def _attempt_for_scope(db: Session, scope_sha256: str) -> CampaignExecutionAttempt | None:
    return db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
        CampaignExecutionAttempt.scope_sha256 == scope_sha256,
    )).scalar_one_or_none()


def _readback_existing(db: Session, plan: CampaignPlan,
                       attempt: CampaignExecutionAttempt) -> dict:
    manifest = (attempt.result_summary or {}).get("manifest")
    if not isinstance(manifest, dict):
        return _fail("plan8_final_v3_attempt_manifest_missing",
                     attempt_id=attempt.id)
    if _hash(manifest) != attempt.scope_sha256:
        return _fail("plan8_final_v3_attempt_scope_mismatch",
                     attempt_id=attempt.id)
    try:
        result = web_agent_service.recover_plan8_final_v3(
            db, payload={"phase": "readback",
                         "scope_sha256": attempt.scope_sha256,
                         "manifest": manifest})
    except Exception as exc:
        result = {"ok": False, "error": type(exc).__name__,
                  "platform_write": False}
    ok, detail = validate_readback(result, manifest, attempt.scope_sha256)
    if not ok:
        prior = dict(attempt.result_summary or {})
        prior["last_readback"] = detail
        attempt.result_summary = prior
        attempt.last_step = "readback_not_complete"
        attempt.error_code = "post_submit_readback_not_complete"
        attempt.web_agent_job_id = str(
            result.get("web_agent_job_id") or "")[:64] or attempt.web_agent_job_id
        db.commit()
        return _fail("plan8_final_v3_readback_not_complete",
                     attempt_id=attempt.id, readback=detail,
                     need_scan=bool(result.get("need_scan")))
    campaign_execution_service.record_platform_terminal(
        db, attempt, state="completed", platform_write_observed=True,
        step="readback_verified", job_id=detail.get("web_agent_job_id"),
        result_summary={**dict(attempt.result_summary or {}),
                        "manifest": manifest, "readback": detail})
    plan.status = "reconciled"
    db.commit()
    return {
        "ok": True, "readback_only": True, "attempt_id": attempt.id,
        "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
        "plan_status": plan.status, "verification": detail,
        "execution_boundary": _boundary(platform_write=False),
    }


def recover_plan8_final_v3(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, recovery_version: int,
        mode: str = "execute") -> dict:
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_status != EXPECTED_STATUS
            or recovery_version != RECOVERY_VERSION
            or mode not in {"execute", "readback"}):
        return _fail("plan8_final_v3_request_not_allowed")
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    identity_ok, identity = _identity_allowed(plan)
    if not identity_ok:
        return _fail("plan8_final_v3_identity_not_allowed", identity=identity)
    attempts = _attempts(db)
    if mode == "readback":
        if len(attempts) != 1:
            return _fail("plan8_final_v3_readback_attempt_ambiguous",
                         attempt_count=len(attempts))
        existing = attempts[0]
        if not existing.write_claimed:
            return _fail("plan8_final_v3_readback_attempt_not_found")
        return _readback_existing(db, plan, existing)
    if attempts:
        if len(attempts) != 1:
            return _fail("plan8_final_v3_attempt_scope_ambiguous",
                         attempt_count=len(attempts))
        existing = attempts[0]
        if existing.state == "completed":
            manifest = (existing.result_summary or {}).get("manifest")
            if not isinstance(manifest, dict) or _hash(manifest) != existing.scope_sha256:
                return _fail("plan8_final_v3_attempt_scope_mismatch",
                             attempt_id=existing.id)
            return {
                "ok": True, "idempotent_replay": True,
                "attempt_id": existing.id, "workflow_key": WORKFLOW_KEY,
                "plan_id": PLAN_ID, "plan_status": plan.status,
                "result": existing.result_summary or {},
                "execution_boundary": _boundary(platform_write=False),
            }
        return _fail("plan8_final_v3_already_claimed_no_retry",
                     attempt_id=existing.id, attempt_state=existing.state,
                     platform_write_observed=existing.platform_write_observed)
    if plan.status != EXPECTED_STATUS:
        return _fail("plan8_final_v3_status_cas_mismatch",
                     actual_status=plan.status)
    prerequisites_ok, prerequisite_detail = _validate_prerequisites(db)
    if not prerequisites_ok:
        return _fail("plan8_final_v3_prerequisite_attempt_mismatch",
                     attempts=prerequisite_detail)
    policy = campaign_policy_service.require_policy()
    policy_sha = str(policy.get("_sha256") or "")
    if policy_sha != EXPECTED_POLICY_SHA256:
        return _fail("plan8_final_v3_policy_changed",
                     actual_policy_sha256=policy_sha)
    target_rows, scope_error = _target_rows(db, plan, identity, policy_sha)
    if scope_error:
        return _fail(**scope_error)
    discount_rows, discount_error = _discount_scope(db, plan)
    if discount_error:
        return _fail(**discount_error)
    manifest = _fixed_manifest(target_rows, discount_rows, policy_sha)
    inspect_scope_sha = _hash(manifest)
    # Do not retain a database row lock while Web-Agent performs the read-only
    # reservation/inspection.
    db.commit()

    # Inspect is deliberately before the durable write attempt. A scheduler or
    # order-task busy response is recoverable and consumes no write claim.
    inspection = web_agent_service.recover_plan8_final_v3(
        db, payload={"phase": "inspect", "scope_sha256": inspect_scope_sha,
                     "manifest": manifest})
    if inspection.get("busy") or inspection.get("pre_write_busy"):
        return _fail("plan8_final_v3_pre_write_busy",
                     busy=inspection, write_claim_created=False)
    inspection_ok, inspection_detail = validate_inspection(
        inspection, manifest, inspect_scope_sha)
    if not inspection_ok:
        return _fail("plan8_final_v3_inspection_blocked",
                     inspection=inspection_detail,
                     need_scan=bool(inspection.get("need_scan")))

    reservation_token = str(inspection["reservation_token"])
    manifest = enrich_manifest_with_inspection(manifest, inspection_detail)
    manifest_sha = _hash(manifest)
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None or plan.status != EXPECTED_STATUS:
        return _fail("plan8_final_v3_state_changed_after_reservation",
                     actual_status=getattr(plan, "status", None))
    post_identity_ok, post_identity = _identity_allowed(plan)
    post_policy_sha = str(
        campaign_policy_service.require_policy().get("_sha256") or "")
    post_rows, post_scope_error = _target_rows(
        db, plan, post_identity, post_policy_sha)
    post_discounts, post_discount_error = _discount_scope(db, plan)
    if (not post_identity_ok or post_policy_sha != policy_sha
            or post_scope_error or post_discount_error
            or _hash(_fixed_manifest(post_rows, post_discounts, post_policy_sha))
            != inspect_scope_sha):
        return _fail(
            "plan8_final_v3_erp_scope_changed_after_reservation",
            identity=post_identity,
            policy_sha256=post_policy_sha,
            signup_scope_error=post_scope_error,
            discount_scope_error=post_discount_error,
        )
    raced = _attempts(db)
    if raced:
        exact = _attempt_for_scope(db, manifest_sha)
        return _fail("plan8_final_v3_attempt_raced_no_write",
                     attempt_count=len(raced), exact_scope_exists=exact is not None)

    attempt = CampaignExecutionAttempt(
        id=secrets.token_hex(12), plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=OPERATION, scope_sha256=manifest_sha, state="prepared",
        write_claimed=False, automatic_retry_allowed=False,
        result_summary={"manifest": manifest, "inspection": inspection_detail},
    )
    db.add(attempt)
    db.commit()
    campaign_execution_service.claim_platform_write(
        db, attempt.id, request_id=f"plan8-final-v3-{secrets.token_hex(8)}")
    plan = db.get(CampaignPlan, PLAN_ID)
    plan.status = "resume_executing"
    db.commit()
    try:
        committed = web_agent_service.recover_plan8_final_v3(
            db, payload={"phase": "commit", "scope_sha256": manifest_sha,
                         "inspect_scope_sha256": inspect_scope_sha,
                         "manifest": manifest, "attempt_id": attempt.id,
                         "reservation_token": reservation_token})
    except Exception as exc:  # fail closed after the one-shot claim
        committed = {"ok": False, "error": type(exc).__name__,
                     "platform_write": None}
    commit_ok, commit_detail = validate_commit(
        committed, manifest, manifest_sha)
    if not commit_ok:
        plan.status = "alarmed"
        db.commit()
        campaign_execution_service.record_platform_terminal(
            db, attempt, state="unknown_no_retry" if
            committed.get("platform_write") is None else "failed_no_retry",
            platform_write_observed=committed.get("platform_write"),
            step=str(committed.get("step") or "plan8_final_v3_commit"),
            error_code=str(committed.get("error") or "commit_failed"),
            job_id=str(committed.get("web_agent_job_id") or "") or None,
            result_summary={"manifest": manifest, "inspection": inspection_detail,
                            "commit": commit_detail})
        return _fail("plan8_final_v3_commit_failed_no_retry",
                     attempt_id=attempt.id, commit=commit_detail)

    try:
        readback = web_agent_service.recover_plan8_final_v3(
            db, payload={"phase": "readback", "scope_sha256": manifest_sha,
                         "manifest": manifest, "attempt_id": attempt.id})
    except Exception as exc:  # commit already succeeded; never resubmit
        readback = {"ok": False, "error": type(exc).__name__,
                    "platform_write": False}
    readback_ok, readback_detail = validate_readback(
        readback, manifest, manifest_sha)
    if not readback_ok:
        plan.status = "alarmed"
        db.commit()
        campaign_execution_service.record_platform_terminal(
            db, attempt, state="failed_no_retry",
            platform_write_observed=True,
            step="plan8_final_v3_readback",
            error_code="post_submit_readback_not_complete",
            job_id=str(readback.get("web_agent_job_id") or "") or None,
            result_summary={"manifest": manifest, "inspection": inspection_detail,
                            "commit": committed, "readback": readback_detail})
        return _fail("plan8_final_v3_readback_not_complete",
                     attempt_id=attempt.id, readback=readback_detail)
    campaign_execution_service.record_platform_terminal(
        db, attempt, state="completed", platform_write_observed=True,
        step="readback_verified",
        job_id=str(readback.get("web_agent_job_id") or "") or None,
        result_summary={"manifest": manifest, "inspection": inspection_detail,
                        "commit": commit_detail, "readback": readback_detail,
                        "finished_at": datetime.now(timezone.utc).isoformat()})
    plan.status = "reconciled"
    db.commit()
    return {
        "ok": True, "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
        "plan_status": plan.status, "attempt_id": attempt.id,
        "scope_sha256": manifest_sha, "verification": readback_detail,
        "execution_boundary": _boundary(platform_write=True),
    }

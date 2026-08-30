"""Exact plan-7 SKU-identity repair followed by one new four-row recovery.

The first correction proved that the four reviewed prices were correct but the
Taobao SKU identities were stale.  This service accepts one immutable official
product export, repairs only the four merchant-code-bound external identities,
and then allows one new single-discount import.  It is intentionally not a
generic SKU remapper or retry route.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import secrets
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignEvidenceSnapshot, CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.taobao_listing import TaobaoListing
from app.services import (
    campaign_discount_audit_service,
    campaign_service,
    settings_service,
    taobao_listing_service,
    web_agent_service,
)


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
EXPECTED_ITEM_ID = "1047741902625"
EXPECTED_ACTIVITY_ID = "143780562424"
EXPECTED_PLAN_STATUS = "alarmed"
EXPECTED_OLD_ATTEMPT_ID = "a701400096c131d9ae2c3e38"
OLD_ATTEMPT_KEY = "campaign_plan7_discount_correction_v1"
IDENTITY_RECEIPT_KEY = "campaign_plan7_discount_sku_identity_v1"
RECOVERY_ATTEMPT_KEY = "campaign_plan7_discount_identity_recovery_v1"
EXPECTED_OFFICIAL_EXPORT_SHA256 = (
    "cdf6502bbf4c048824a0ad5f1545d6335faa117a854f3c624773c1e610a9a72b"
)
EXPECTED_ORIGINAL_SNAPSHOT_ID = 1
EXPECTED_ORIGINAL_SNAPSHOT_SHA256 = (
    "34e5fb410ca0bed56baca4ef0681fcbfc7a8c3b81ebaa5397a51e579f32a8211"
)
EXPECTED_ORIGINAL_SCOPE_SHA256 = (
    "599fa440ba4f7e42aab4dd39423fa807ec85d4964a8df5169303ffb9c0517a18"
)
EXPECTED_NEW_FULL_SCOPE_SHA256 = (
    "ae7b9683a7de00050e3911072b5ab2d7d678e4ea5b75fb9ff55f1edebab71598"
)
EXPECTED_NEW_MISSING_SCOPE_SHA256 = (
    "80e603ca57aa2974ab892f9ad1738e3dbd3b00d3b026ad80dd7aed642085371a"
)

EXPECTED_ROWS = (
    {
        "item_id": EXPECTED_ITEM_ID,
        "old_sku_id": "6279984722445",
        "sku_id": "6127845548093",
        "sku_code": "PFG2521002122211",
        "spec": "颜色分类:蜂蜜餐桌-1.2米;",
        "daily": "3390.00", "deduct": "612.63",
        "official": "339.00", "final": "2438.37",
    },
    {
        "item_id": EXPECTED_ITEM_ID,
        "old_sku_id": "6279984722446",
        "sku_id": "6127845548094",
        "sku_code": "PFG2521002122212",
        "spec": "颜色分类:蜂蜜餐桌-1.4米;",
        "daily": "3465.00", "deduct": "627.08",
        "official": "347.00", "final": "2490.92",
    },
    {
        "item_id": EXPECTED_ITEM_ID,
        "old_sku_id": "6279984722447",
        "sku_id": "6127845548095",
        "sku_code": "PFG2521002122213",
        "spec": "颜色分类:蜂蜜餐桌-1.6米;",
        "daily": "3577.50", "deduct": "644.50",
        "official": "358.00", "final": "2575.00",
    },
    {
        "item_id": EXPECTED_ITEM_ID,
        "old_sku_id": "6279984722448",
        "sku_id": "6127845548096",
        "sku_code": "PFG2521002122214",
        "spec": "颜色分类:蜂蜜餐桌-1.8米;",
        "daily": "3667.50", "deduct": "662.44",
        "official": "367.00", "final": "2638.06",
    },
)


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def _money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"


def _boundary(*, platform_read: bool = False,
              platform_write: bool = False,
              identity_write: bool = False) -> dict:
    return {
        "plan7_only": True,
        "exact_item_id": EXPECTED_ITEM_ID,
        "exact_row_count": 4,
        "official_export_sha256": EXPECTED_OFFICIAL_EXPORT_SHA256,
        "platform_read": bool(platform_read),
        "platform_write": bool(platform_write),
        "account_action": bool(platform_write),
        "erp_sku_identity_write": bool(identity_write),
        "price_change": False,
        "sku_rotation": False,
        "same_merchant_code_identity_repair": True,
        "official_signup": False,
        "withdraw_pause_remove": False,
        "touches_existing_384_rows": False,
        "touches_plan8": False,
        "notification": False,
        "automatic_retry": False,
        "post_submit_readback_required": True,
    }


def _fail(error: str, *, platform_read: bool = False,
          platform_write: bool = False, identity_write: bool = False,
          **detail) -> dict:
    return {
        "ok": False, "error": error, **detail,
        "execution_boundary": _boundary(
            platform_read=platform_read,
            platform_write=platform_write,
            identity_write=identity_write),
    }


def _load_json_setting(db: Session, key: str) -> dict | None:
    raw = settings_service.get(db, key, env_fallback=False)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"status": "invalid"}
    return value if isinstance(value, dict) else {"status": "invalid"}


def _save_json_setting(db: Session, key: str, value: dict,
                       description: str) -> None:
    settings_service.set_value(
        db, key,
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str),
        description=description,
    )


def _decode_export(encoded: str, expected_sha256: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("official_product_export_base64_invalid") from exc
    if not raw or len(raw) > 2_000_000:
        raise ValueError("official_product_export_size_not_allowed")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("official_product_export_sha256_mismatch")
    return raw


def _validate_official_export(raw: bytes) -> list[dict]:
    records, warnings = taobao_listing_service.parse_rows(raw)
    if warnings or len(records) != 9 or {
            str(row.get("taobao_item_id") or "") for row in records
    } != {EXPECTED_ITEM_ID}:
        raise ValueError("official_product_export_scope_mismatch")
    by_code = {
        str(row.get("sku_code_raw") or ""): row
        for row in records if row.get("sku_code_raw")
    }
    old_ids = {
        str(row.get("taobao_sku_id") or "")
        for row in records if not row.get("sku_code_raw")
    }
    expected_old_ids = {row["old_sku_id"] for row in EXPECTED_ROWS}
    if old_ids != expected_old_ids:
        raise ValueError("official_product_export_old_identity_mismatch")
    for expected in EXPECTED_ROWS:
        row = by_code.get(expected["sku_code"])
        if (
            row is None
            or str(row.get("taobao_sku_id") or "") != expected["sku_id"]
            or str(row.get("sku_spec") or "") != expected["spec"]
            or str(row.get("merchant_code") or "") != "PFG25210021222"
        ):
            raise ValueError("official_product_export_current_identity_mismatch")
    placeholder = by_code.get("PFG2521002122299")
    if (
        placeholder is None
        or str(placeholder.get("taobao_sku_id") or "") != "6076826145980"
        or str(placeholder.get("sku_spec") or "") != "颜色分类:尺寸定制;"
    ):
        raise ValueError("official_product_export_placeholder_mismatch")
    if set(by_code) != {
            *(row["sku_code"] for row in EXPECTED_ROWS),
            "PFG2521002122299"}:
        raise ValueError("official_product_export_merchant_scope_mismatch")
    return records


def _plan_guard(db: Session, *, lock: bool = False) -> CampaignPlan | None:
    query = select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    )
    if lock:
        query = query.with_for_update()
    plan = db.execute(query).scalar_one_or_none()
    if plan is None:
        return None
    if (
        plan.status != EXPECTED_PLAN_STATUS
        or plan.campaign_type != "super_reduce"
        or plan.platform_activity_mode != "long_running_update"
        or str(plan.qn_campaign_title or "").strip() != "超级立减"
        or plan.start_at is None
        or _now_shanghai() >= plan.start_at
    ):
        return None
    return plan


def _old_failure_guard(db: Session) -> bool:
    attempt = _load_json_setting(db, OLD_ATTEMPT_KEY) or {}
    return (
        attempt.get("status") == "failed_terminal_no_retry"
        and attempt.get("attempt_id") == EXPECTED_OLD_ATTEMPT_ID
        and attempt.get("submitted") is False
        and attempt.get("terminal_job_id") == "job2"
        and attempt.get("terminal_evidence_request_id")
        == "single-discount-terminal-2ff91afda28d24a1"
        and attempt.get("missing_scope_sha256")
        == "2ef18e9537abae8af10ec1a0580336e2377b1ca3a7da38d4247a9bc7bf4a9739"
    )


def _original_snapshot_guard(db: Session) -> bool:
    snapshot = db.get(CampaignEvidenceSnapshot, EXPECTED_ORIGINAL_SNAPSHOT_ID)
    return bool(snapshot and (
        snapshot.plan_id == PLAN_ID
        and snapshot.workflow_key == WORKFLOW_KEY
        and snapshot.scope_sha256 == EXPECTED_ORIGINAL_SCOPE_SHA256
        and snapshot.artifact_sha256 == EXPECTED_ORIGINAL_SNAPSHOT_SHA256
        and snapshot.artifact_blob is not None
        and hashlib.sha256(snapshot.artifact_blob).hexdigest()
        == EXPECTED_ORIGINAL_SNAPSHOT_SHA256
    ))


def _upsert_listing_evidence(db: Session, records: list[dict]) -> None:
    product = db.execute(select(PricingSku).where(
        PricingSku.sku_code == EXPECTED_ROWS[0]["sku_code"]
    )).scalar_one()
    for raw in records:
        values = dict(raw)
        sku_code = values.pop("sku_code_raw", None)
        existing = db.execute(select(TaobaoListing).where(
            TaobaoListing.taobao_item_id == EXPECTED_ITEM_ID,
            TaobaoListing.taobao_sku_id == values["taobao_sku_id"],
        )).scalar_one_or_none()
        row = existing or TaobaoListing(
            taobao_item_id=EXPECTED_ITEM_ID,
            taobao_sku_id=values["taobao_sku_id"],
        )
        for field in ("title", "merchant_code", "sku_spec", "category_name",
                      "list_price", "sku_price", "stock"):
            setattr(row, field, values.get(field))
        row.sku_code = sku_code if sku_code in {
            *(item["sku_code"] for item in EXPECTED_ROWS),
            "PFG2521002122299"} else None
        row.product_code = product.product_code
        row.matched = bool(row.sku_code)
        if existing is None:
            db.add(row)


def _apply_identity_correction(db: Session, *, plan: CampaignPlan,
                               raw: bytes, records: list[dict]) -> dict:
    receipt = _load_json_setting(db, IDENTITY_RECEIPT_KEY)
    promos = db.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code.in_(
            [row["sku_code"] for row in EXPECTED_ROWS])
    ).with_for_update()).scalars().all()
    by_code = {row.sku_code: row for row in promos}
    if set(by_code) != {row["sku_code"] for row in EXPECTED_ROWS}:
        raise ValueError("sku_identity_pricing_scope_missing")
    if receipt:
        if (
            receipt.get("status") != "completed"
            or receipt.get("official_export_sha256")
            != EXPECTED_OFFICIAL_EXPORT_SHA256
            or any(by_code[row["sku_code"]].taobao_sku_id != row["sku_id"]
                   for row in EXPECTED_ROWS)
        ):
            raise ValueError("sku_identity_receipt_conflict")
        return receipt
    if any(
        by_code[row["sku_code"]].taobao_item_id != EXPECTED_ITEM_ID
        or by_code[row["sku_code"]].taobao_sku_id != row["old_sku_id"]
        for row in EXPECTED_ROWS
    ):
        raise ValueError("sku_identity_old_value_cas_mismatch")
    for expected in EXPECTED_ROWS:
        by_code[expected["sku_code"]].taobao_sku_id = expected["sku_id"]
    _upsert_listing_evidence(db, records)
    evidence_rows = [{
        "item_id": row["item_id"],
        "sku_code": row["sku_code"],
        "old_sku_id": row["old_sku_id"],
        "current_sku_id": row["sku_id"],
        "spec": row["spec"],
        "classification": "same_merchant_code_external_identity_repair",
    } for row in EXPECTED_ROWS]
    snapshot = campaign_discount_audit_service._persist(
        db, plan=plan,
        evidence_type="taobao_sku_identity_correction",
        request_id=f"plan7-sku-identity-{secrets.token_hex(6)}",
        web_agent_job_id=None,
        scope_digest=EXPECTED_NEW_MISSING_SCOPE_SHA256,
        status="complete",
        summary={"item_count": 1, "corrected_skus": 4,
                 "official_export_sha256": EXPECTED_OFFICIAL_EXPORT_SHA256},
        rows=evidence_rows, failure_rows=[],
        boundary=_boundary(identity_write=True),
        artifact={
            "kind": "taobao_official_product_export_xlsx",
            "filename": "plan7-live-sku-identity-1047741902625.xlsx",
            "size": len(raw),
            "sha256": EXPECTED_OFFICIAL_EXPORT_SHA256,
            "content_b64": base64.b64encode(raw).decode("ascii"),
        },
    )
    receipt = {
        "status": "completed",
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "item_id": EXPECTED_ITEM_ID,
        "official_export_sha256": EXPECTED_OFFICIAL_EXPORT_SHA256,
        "evidence_snapshot_id": snapshot.id,
        "mapping": evidence_rows,
    }
    _save_json_setting(
        db, IDENTITY_RECEIPT_KEY, receipt,
        "计划7蜂蜜餐桌4行淘宝SKU同商家编码身份修正回执（不含凭据）")
    db.commit()
    return receipt


def _current_rows(db: Session, plan: CampaignPlan) -> tuple[list[dict], list[dict]]:
    rows, _ = campaign_service.build_discount_rows(db, plan)
    full_scope = [{
        "item_id": str(row.get("taobao_item_id") or ""),
        "sku_id": str(row.get("taobao_sku_id") or ""),
        "expected_deduct": _money(row.get("deduct")),
    } for row in rows]
    if (
        len(full_scope) != 388
        or len({row["item_id"] for row in full_scope}) != 54
        or campaign_discount_audit_service.scope_sha256(full_scope)
        != EXPECTED_NEW_FULL_SCOPE_SHA256
    ):
        raise ValueError("sku_identity_recovery_full_scope_drift")
    current_ids = {row["sku_id"] for row in EXPECTED_ROWS}
    selected = [row for row in rows if (
        str(row.get("taobao_item_id") or "") == EXPECTED_ITEM_ID
        and str(row.get("taobao_sku_id") or "") in current_ids
    )]
    canonical = sorted(({
        "item_id": str(row.get("taobao_item_id") or ""),
        "sku_id": str(row.get("taobao_sku_id") or ""),
        "sku_code": str(row.get("sku_code") or ""),
        "daily": _money(row.get("calculation_base")),
        "deduct": _money(row.get("deduct")),
        "official": _money(row.get("official")),
        "final": _money(row.get("target_price")),
        "kind": str(row.get("kind") or ""),
        "concession": _money(row.get("concession") or 0),
    } for row in selected), key=lambda row: row["sku_id"])
    expected = sorted(({
        "item_id": row["item_id"], "sku_id": row["sku_id"],
        "sku_code": row["sku_code"], "daily": row["daily"],
        "deduct": row["deduct"], "official": row["official"],
        "final": row["final"], "kind": "nosales", "concession": "0.00",
    } for row in EXPECTED_ROWS), key=lambda row: row["sku_id"])
    if canonical != expected or len(selected) != 4:
        raise ValueError("sku_identity_recovery_price_scope_drift")
    if any(
        Decimal(row["daily"]) - Decimal(row["official"])
        - Decimal(row["deduct"]) != Decimal(row["final"])
        for row in canonical
    ):
        raise ValueError("sku_identity_recovery_final_price_math_mismatch")
    return selected, canonical


def _scope_rows() -> list[dict]:
    return [{
        "item_id": row["item_id"], "sku_id": row["sku_id"],
        "expected_deduct": row["deduct"],
    } for row in EXPECTED_ROWS]


def _platform_read(db: Session, plan: CampaignPlan) -> dict:
    return web_agent_service.audit_plan7_single_discount(
        db, workflow_key=WORKFLOW_KEY, scope=_scope_rows(),
        scope_sha256=EXPECTED_NEW_MISSING_SCOPE_SHA256,
        start_at=plan.start_at.strftime("%Y-%m-%d %H:%M:%S"),
        end_at=plan.end_at.strftime("%Y-%m-%d %H:%M:%S"),
    )


def _read_class(result: dict, required: str) -> bool:
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    actual = sorted(({
        "item_id": str(row.get("item_id") or ""),
        "sku_id": str(row.get("sku_id") or ""),
        "expected_deduct": _money(row.get("expected_deduct")),
    } for row in rows), key=lambda row: row["sku_id"])
    expected = sorted(_scope_rows(), key=lambda row: row["sku_id"])
    return bool(
        result.get("ok") is True and actual == expected and len(rows) == 4
        and all(
            row.get("classification") == required
            and (required == "missing" or (
                _money(row.get("actual_deduct"))
                == _money(row.get("expected_deduct"))
                and str(row.get("status") or "") == "未开始"))
            for row in rows
        )
    )


def recover_plan7_single_discount_identity(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_old_attempt_id: str, official_product_export_sha256: str,
        official_product_export_b64: str,
        expected_new_scope_sha256: str) -> dict:
    """Apply the exact identity repair and permit one new four-row import."""
    if (
        workflow_key != WORKFLOW_KEY
        or expected_plan_id != PLAN_ID
        or expected_old_attempt_id != EXPECTED_OLD_ATTEMPT_ID
        or official_product_export_sha256 != EXPECTED_OFFICIAL_EXPORT_SHA256
        or expected_new_scope_sha256 != EXPECTED_NEW_MISSING_SCOPE_SHA256
    ):
        return _fail("sku_identity_recovery_request_not_allowed")
    try:
        raw = _decode_export(
            official_product_export_b64, official_product_export_sha256)
        records = _validate_official_export(raw)
    except ValueError as exc:
        return _fail(str(exc))
    plan = _plan_guard(db, lock=True)
    if plan is None:
        return _fail("sku_identity_recovery_plan_identity_not_allowed")
    if not _old_failure_guard(db) or not _original_snapshot_guard(db):
        return _fail("sku_identity_recovery_prior_evidence_mismatch")
    try:
        identity_receipt = _apply_identity_correction(
            db, plan=plan, raw=raw, records=records)
        plan = _plan_guard(db, lock=True)
        if plan is None:
            return _fail(
                "sku_identity_recovery_plan_changed_after_identity_write",
                identity_write=True)
        raw_rows, canonical = _current_rows(db, plan)
    except ValueError as exc:
        db.rollback()
        return _fail(str(exc), identity_write=bool(
            _load_json_setting(db, IDENTITY_RECEIPT_KEY)))
    attempt = _load_json_setting(db, RECOVERY_ATTEMPT_KEY)
    if attempt:
        if attempt.get("status") == "completed":
            return {
                "ok": True, "idempotent_replay": True,
                "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
                "identity_receipt": identity_receipt, "attempt": attempt,
                "execution_boundary": _boundary(identity_write=True),
            }
        return _fail(
            "sku_identity_recovery_attempt_already_claimed_no_retry",
            identity_write=True, attempt_id=attempt.get("attempt_id"),
            attempt_status=attempt.get("status"))
    db.commit()
    pre_read = _platform_read(db, plan)
    if _read_class(pre_read, "present_not_effective"):
        final = {
            "status": "completed", "attempt_id": secrets.token_hex(12),
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
            "rows": canonical, "submitted": False,
            "already_exact_no_write": True,
            "pre_submit_web_agent_job_id": pre_read.get("web_agent_job_id"),
            "automatic_retry": False,
        }
        _save_json_setting(
            db, RECOVERY_ATTEMPT_KEY, final,
            "计划7蜂蜜餐桌SKU身份修正后4行一次性恢复回执（不含凭据）")
        db.commit()
        return {
            "ok": True, "already_exact_no_write": True,
            "identity_receipt": identity_receipt, "attempt": final,
            "execution_boundary": _boundary(
                platform_read=True, identity_write=True),
        }
    if not _read_class(pre_read, "missing"):
        return _fail(
            pre_read.get("error") or "sku_identity_recovery_pre_read_not_allowed",
            platform_read=True, identity_write=True,
            web_agent_job_id=pre_read.get("web_agent_job_id"),
            rows=pre_read.get("rows"))
    plan = _plan_guard(db, lock=True)
    if plan is None or _load_json_setting(db, RECOVERY_ATTEMPT_KEY):
        return _fail(
            "sku_identity_recovery_attempt_raced_no_write",
            platform_read=True, identity_write=True)
    try:
        raw_rows_after, canonical_after = _current_rows(db, plan)
    except ValueError as exc:
        return _fail(
            str(exc), platform_read=True, identity_write=True)
    if canonical_after != canonical:
        return _fail(
            "sku_identity_recovery_scope_changed_after_read",
            platform_read=True, identity_write=True)
    attempt_id = secrets.token_hex(12)
    claimed = {
        "status": "claimed", "attempt_id": attempt_id,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
        "old_attempt_id": EXPECTED_OLD_ATTEMPT_ID,
        "official_export_sha256": EXPECTED_OFFICIAL_EXPORT_SHA256,
        "new_scope_sha256": EXPECTED_NEW_MISSING_SCOPE_SHA256,
        "rows": canonical, "submitted": None,
        "pre_submit_web_agent_job_id": pre_read.get("web_agent_job_id"),
        "automatic_retry": False,
    }
    _save_json_setting(
        db, RECOVERY_ATTEMPT_KEY, claimed,
        "计划7蜂蜜餐桌SKU身份修正后4行一次性恢复回执（不含凭据）")
    db.commit()
    target_xlsx = campaign_service._build_discount_xlsx(raw_rows_after)
    if campaign_discount_audit_service.xlsx_scope_sha256(
            target_xlsx) != EXPECTED_NEW_MISSING_SCOPE_SHA256:
        failed = {**claimed, "status": "failed_target_xlsx_drift",
                  "finished_at": datetime.now(timezone.utc).isoformat(),
                  "submitted": False}
        _save_json_setting(db, RECOVERY_ATTEMPT_KEY, failed,
                           "计划7SKU身份修正后恢复失败回执")
        db.commit()
        return _fail(
            "sku_identity_recovery_target_xlsx_drift",
            platform_read=True, identity_write=True, attempt=failed)
    try:
        terminal = campaign_service._upload_and_wait(
            db, "single_item_discount", "commit", target_xlsx,
            plan.start_at.strftime("%Y-%m-%d %H:%M:%S"),
            plan.end_at.strftime("%Y-%m-%d %H:%M:%S"),
            plan=plan, expected_rows=4,
            ignore_plan_discount_activity=True)
    except Exception as exc:  # platform outcome may be unknown
        failed = {**claimed, "status": "failed_unknown_outcome",
                  "finished_at": datetime.now(timezone.utc).isoformat(),
                  "submitted": None, "error_type": type(exc).__name__}
        _save_json_setting(db, RECOVERY_ATTEMPT_KEY, failed,
                           "计划7SKU身份修正后恢复未知终态回执")
        db.commit()
        return _fail(
            "sku_identity_recovery_unknown_outcome_no_retry",
            platform_read=True, platform_write=True,
            identity_write=True, attempt=failed)
    submitted = bool(terminal.get("submitted"))
    if not terminal.get("ok"):
        failed = {
            **claimed, "status": "failed_terminal_no_retry",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "submitted": submitted, "terminal_job_id": terminal.get("job"),
            "terminal_error": terminal.get("error"),
            "terminal_evidence_request_id": terminal.get("evidence_request_id"),
        }
        _save_json_setting(db, RECOVERY_ATTEMPT_KEY, failed,
                           "计划7SKU身份修正后恢复平台失败回执")
        db.commit()
        return _fail(
            "sku_identity_recovery_terminal_failed_no_retry",
            platform_read=True, platform_write=submitted,
            identity_write=True, attempt=failed, terminal=terminal)
    post_read = _platform_read(db, plan)
    post_ok = _read_class(post_read, "present_not_effective")
    artifact = post_read.get("artifact") if isinstance(
        post_read.get("artifact"), dict) else {}
    if post_ok and (
        not artifact.get("content_b64") or not artifact.get("sha256")
        or not artifact.get("size")
    ):
        post_ok = False
    post_snapshot_id = None
    if post_ok:
        receipt = campaign_discount_audit_service._persist(
            db, plan=plan,
            evidence_type="single_item_discount_identity_recovery_readback",
            request_id=f"plan7-discount-identity-recovery-{secrets.token_hex(6)}",
            web_agent_job_id=post_read.get("web_agent_job_id"),
            scope_digest=EXPECTED_NEW_MISSING_SCOPE_SHA256,
            status="complete", summary=post_read.get("platform_summary"),
            rows=post_read.get("rows"), failure_rows=[],
            boundary=_boundary(
                platform_read=True, platform_write=True,
                identity_write=True), artifact=artifact)
        post_snapshot_id = receipt.id
    final = {
        **claimed,
        "status": "completed" if post_ok else "failed_post_submit_readback",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "submitted": True, "terminal_job_id": terminal.get("job"),
        "terminal_evidence_request_id": terminal.get("evidence_request_id"),
        "post_submit_web_agent_job_id": post_read.get("web_agent_job_id"),
        "post_submit_artifact_sha256": artifact.get("sha256"),
        "post_submit_snapshot_id": post_snapshot_id,
        "result_error": None if post_ok else (
            post_read.get("error") or "sku_identity_recovery_post_read_failed"),
    }
    _save_json_setting(db, RECOVERY_ATTEMPT_KEY, final,
                       "计划7SKU身份修正后恢复终态回执")
    db.commit()
    if not post_ok:
        return _fail(
            "sku_identity_recovery_post_submit_readback_failed_no_retry",
            platform_read=True, platform_write=True,
            identity_write=True, attempt=final, terminal=terminal,
            post_submit_readback=post_read)
    return {
        "ok": True, "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
        "identity_receipt": identity_receipt, "attempt": final,
        "terminal": terminal,
        "post_submit_readback": {
            "snapshot_id": post_snapshot_id,
            "web_agent_job_id": post_read.get("web_agent_job_id"),
            "platform_summary": post_read.get("platform_summary"),
            "rows": post_read.get("rows"),
            "artifact": {key: value for key, value in artifact.items()
                         if key != "content_b64"},
        },
        "execution_boundary": _boundary(
            platform_read=True, platform_write=True, identity_write=True),
    }

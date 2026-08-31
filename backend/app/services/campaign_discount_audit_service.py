"""Plan-7 single-item-discount evidence audit and append-only receipts."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.campaign import CampaignEvidenceSnapshot, CampaignPlan
from app.services import campaign_service, web_agent_service


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
EXPECTED_SCOPE_SHA256 = (
    "38c967e5a08acd378ff6c4778494f450613926a9cf32e7ee51b51a1d81b75d8f"
)
EXEMPT_ITEM_ID = "805268708396"


def _fmt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _scope_rows(db: Session, plan: CampaignPlan) -> list[dict[str, str]]:
    rows, _ = campaign_service.build_discount_rows(db, plan)
    return sorted(({
        "item_id": str(row.get("taobao_item_id") or "").strip(),
        "sku_id": str(row.get("taobao_sku_id") or "").strip(),
        "expected_deduct": f"{float(row.get('deduct')):.2f}",
    } for row in rows), key=lambda row: (row["item_id"], row["sku_id"]))


def scope_sha256(rows: list[dict]) -> str:
    payload = [
        [str(row["item_id"]), str(row["sku_id"]),
         f"{float(row['expected_deduct']):.2f}"]
        for row in sorted(rows, key=lambda value: (
            str(value["item_id"]), str(value["sku_id"])))
    ]
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def xlsx_scope_sha256(content: bytes) -> str:
    """Canonical item/SKU/deduct digest, independent of XLSX zip metadata."""
    from io import BytesIO
    import openpyxl

    workbook = openpyxl.load_workbook(
        BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = []
        for raw in sheet.iter_rows(min_row=2, values_only=True):
            if not raw or all(value in (None, "") for value in raw):
                continue
            rows.append({
                "item_id": str(raw[0] or "").strip(),
                "sku_id": str(raw[1] or "").strip(),
                "expected_deduct": f"{float(raw[2]):.2f}",
            })
        return scope_sha256(rows)
    finally:
        workbook.close()


def readonly_boundary() -> dict:
    return {
        "plan7_only": True,
        "platform_read": True,
        "platform_write": False,
        "erp_evidence_write": True,
        "account_action": False,
        "price_change": False,
        "selection": False,
        "upload": False,
        "submit": False,
        "enable_disable_delete": False,
        "touches_plan8": False,
        "notification": False,
        "automatic_retry": False,
    }


def _decode_artifact(artifact: dict | None) -> bytes | None:
    encoded = (artifact or {}).get("content_b64") or (artifact or {}).get("xlsx_b64")
    if not encoded:
        return None
    return base64.b64decode(encoded, validate=True)


def _persist(
        db: Session, *, plan: CampaignPlan, evidence_type: str,
        request_id: str, web_agent_job_id: str | None, scope_digest: str,
        status: str, summary: dict | None, rows: list | None,
        failure_rows: list | None, boundary: dict,
        artifact: dict | None = None,
        failure_artifact: dict | None = None,
) -> CampaignEvidenceSnapshot:
    raw = _decode_artifact(artifact)
    failed_raw = _decode_artifact(failure_artifact)
    for meta, content in ((artifact, raw), (failure_artifact, failed_raw)):
        if meta and content is not None:
            if int(meta.get("size") or meta.get("xlsx_size") or -1) != len(content):
                raise ValueError("campaign_evidence_artifact_size_mismatch")
            if str(meta.get("sha256") or "").lower() != hashlib.sha256(content).hexdigest():
                raise ValueError("campaign_evidence_artifact_sha256_mismatch")
    row = CampaignEvidenceSnapshot(
        plan_id=plan.id,
        workflow_key=plan.workflow_key or "",
        evidence_type=evidence_type,
        request_id=request_id,
        web_agent_job_id=web_agent_job_id,
        scope_sha256=scope_digest,
        result_status=status,
        platform_summary=summary or {},
        rows=rows or [],
        failure_rows=failure_rows or [],
        execution_boundary=boundary,
        artifact_kind=(artifact or {}).get("kind"),
        artifact_filename=(artifact or {}).get("filename"),
        artifact_sha256=(artifact or {}).get("sha256"),
        artifact_size=len(raw) if raw is not None else None,
        artifact_blob=raw,
        failure_artifact_filename=(failure_artifact or {}).get("filename"),
        failure_artifact_sha256=(failure_artifact or {}).get("sha256"),
        failure_artifact_size=(len(failed_raw) if failed_raw is not None else None),
        failure_artifact_blob=failed_raw,
    )
    db.add(row)
    db.flush()
    return row


def audit_plan7_single_discount(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_scope_sha256: str) -> dict:
    """Read current platform state and persist a complete immutable snapshot."""
    boundary = readonly_boundary()
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_scope_sha256 != EXPECTED_SCOPE_SHA256):
        return {"ok": False, "error": "plan7_discount_audit_request_not_allowed",
                "execution_boundary": {**boundary, "platform_read": False}}
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    )).scalar_one_or_none()
    if plan is None:
        return {"ok": False, "error": "workflow_not_found",
                "execution_boundary": {**boundary, "platform_read": False}}
    official_scope = campaign_service.official_scope_for_plan(plan)
    if (plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or EXEMPT_ITEM_ID not in official_scope["exempt_items"]):
        return {"ok": False, "error": "plan7_discount_audit_identity_not_allowed",
                "execution_boundary": {**boundary, "platform_read": False}}
    scope = _scope_rows(db, plan)
    digest = scope_sha256(scope)
    if (digest != EXPECTED_SCOPE_SHA256 or len(scope) != 392
            or len({row["item_id"] for row in scope}) != 55
            or any(row["item_id"] == EXEMPT_ITEM_ID for row in scope)):
        return {
            "ok": False, "error": "plan7_discount_audit_scope_drift",
            "actual_scope_sha256": digest,
            "actual_rows": len(scope),
            "actual_items": len({row["item_id"] for row in scope}),
            "execution_boundary": {**boundary, "platform_read": False},
        }
    result = web_agent_service.audit_plan7_single_discount(
        db, workflow_key=WORKFLOW_KEY, scope=scope,
        scope_sha256=digest, start_at=_fmt(plan.start_at), end_at=_fmt(plan.end_at))
    if not result.get("ok"):
        return {**result, "execution_boundary": boundary}
    evidence_rows = result.get("rows") or []
    expected_keys = {(row["item_id"], row["sku_id"]) for row in scope}
    actual_keys = [
        (str(row.get("item_id") or ""), str(row.get("sku_id") or ""))
        for row in evidence_rows if isinstance(row, dict)
    ]
    allowed_classes = {
        "correct_effective", "amount_mismatch", "missing",
        "present_not_effective", "platform_unreadable", "platform_ambiguous",
    }
    if (len(actual_keys) != len(expected_keys)
            or set(actual_keys) != expected_keys
            or len(set(actual_keys)) != len(actual_keys)
            or any(row.get("classification") not in allowed_classes
                   for row in evidence_rows)):
        return {
            "ok": False,
            "error": "plan7_discount_audit_incomplete_platform_result",
            "expected_rows": len(expected_keys),
            "actual_rows": len(actual_keys),
            "execution_boundary": boundary,
        }
    artifact = result.get("artifact") or {}
    request_id = f"plan7-discount-audit-{secrets.token_hex(6)}"
    snapshot = _persist(
        db, plan=plan, evidence_type="single_item_discount_readback",
        request_id=request_id,
        web_agent_job_id=result.get("web_agent_job_id"),
        scope_digest=digest,
        status=("complete" if all(
            row.get("classification") == "correct_effective"
            for row in evidence_rows) else "differences"),
        summary=result.get("platform_summary"),
        rows=evidence_rows, failure_rows=[], boundary=boundary,
        artifact=artifact,
    )
    db.commit()
    return {
        "ok": True,
        "request_id": request_id,
        "snapshot_id": snapshot.id,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "scope_sha256": digest,
        "platform_summary": result.get("platform_summary"),
        "rows": evidence_rows,
        "artifact": {key: value for key, value in artifact.items()
                     if key != "content_b64"},
        "web_agent_job_id": result.get("web_agent_job_id"),
        "execution_boundary": boundary,
    }


def persist_single_discount_terminal(
        *, plan_id: int, workflow_key: str, job_id: str | None,
        target_xlsx: bytes, result: dict) -> str | None:
    """Durably record every future commit terminal, including partial failures.

    A separate transaction is intentional: the caller may fail closed and roll
    back its business state after the platform has already written some rows.
    """
    if not workflow_key or not target_xlsx:
        return None
    request_id = f"single-discount-terminal-{secrets.token_hex(8)}"
    target_sha = hashlib.sha256(target_xlsx).hexdigest()
    target_scope_sha = xlsx_scope_sha256(target_xlsx)
    final_import = result.get("final_import") or {}
    failure_artifact = final_import.get("failed_artifact") or {}
    target_artifact = {
        "kind": "submitted_target_xlsx",
        "filename": "campaign_single_item_discount.xlsx",
        "size": len(target_xlsx),
        "sha256": target_sha,
        "content_b64": base64.b64encode(target_xlsx).decode("ascii"),
    }
    boundary = {
        "platform_write": bool(result.get("submitted")),
        "account_action": bool(result.get("submitted")),
        "erp_evidence_write": True,
        "automatic_retry": False,
    }
    with SessionLocal() as receipt_db:
        plan = receipt_db.get(CampaignPlan, plan_id)
        if plan is None or plan.workflow_key != workflow_key:
            return None
        snapshot = _persist(
            receipt_db, plan=plan,
            evidence_type="single_item_discount_terminal",
            request_id=request_id, web_agent_job_id=job_id,
            scope_digest=target_scope_sha,
            status=str(final_import.get("state") or (
                "unknown" if result.get("submitted") else "failed")),
            summary={
                "validation": result.get("validation"),
                "final_import": {key: value for key, value in final_import.items()
                                 if key not in {"failed_artifact", "failed_file"}},
                "final_step_error": result.get("final_step_error"),
            },
            rows=[], failure_rows=final_import.get("failed_rows") or [],
            boundary=boundary, artifact=target_artifact,
            failure_artifact=failure_artifact,
        )
        receipt_db.commit()
        return snapshot.request_id

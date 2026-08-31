"""One-shot plan-7 correction from mid-buyer targets to ERP small-promo.

The scope is deliberately frozen to 20 existing SKU rows in activity
143780562424.  It cannot add products, change list/signup prices, touch the
excluded accessory or placeholders, create/withdraw/pause/delete activities,
or retry a claimed platform write.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import (
    CampaignEvidenceSnapshot,
    CampaignExecutionAttempt,
    CampaignPlan,
)
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_discount_audit_service, web_agent_service


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
OPERATION = "plan7_small_promo_correct"
ACTIVITY_IDS = ("143780562424", "143936811502", "143939511827")
TARGET_ACTIVITY_ID = "143780562424"
START_AT = "2026-09-01 00:00:00"
END_AT = "2026-09-05 23:59:59"
SOURCE_SNAPSHOT_ID = 1
SOURCE_SNAPSHOT_REQUEST_ID = "plan7-discount-audit-464fc409dce0"
SOURCE_SNAPSHOT_ARTIFACT_SHA256 = (
    "34e5fb410ca0bed56baca4ef0681fcbfc7a8c3b81ebaa5397a51e579f32a8211"
)
MANIFEST_SHA256 = "3ebbbdc481a833b7396aa4cbe8a50285e5baad0125904fab1573811b695ffd00"
CURRENT_SCOPE_SHA256 = "77db342043461529ee63e715b29626be2d40e783550eb4ba5650f2cff092525a"
TARGET_SCOPE_SHA256 = "31ae8b34358cdd2378320bdb625fb754eecb20a20bfc77d854028bba653f63a6"
FORBIDDEN_SKU_ID = "6280268983408"
FORBIDDEN_SKU_CODE = "PPS2633011022619"


MANIFEST_ROWS = (
    ("1036273574687", "6070339397134", "PPS2633011022611", "樱桃木静音床-1.2米-榉木铺板", "7860.00", "5895.00", "3678.57", "3660.00", "1626.43", "1645.00"),
    ("1036273574687", "6070339397135", "PPS2633011022615", "樱桃木静音床-1.2米-松木铺板", "6700.00", "5025.00", "3132.04", "3120.00", "1389.96", "1402.00"),
    ("1036273574687", "6070339397136", "PPS2633011022612", "樱桃木静音床-1.35米-榉木铺板", "7990.00", "5992.50", "3731.12", "3720.00", "1661.38", "1672.50"),
    ("1036273574687", "6070339397137", "PPS2633011022616", "樱桃木静音床-1.35米-松木铺板", "6830.00", "5122.50", "3195.10", "3180.00", "1414.40", "1429.50"),
    ("1036273574687", "6070339397138", "PPS2633011022613", "樱桃木静音床-1.5米-榉木铺板", "8270.00", "6202.50", "3867.75", "3850.00", "1713.75", "1731.50"),
    ("1036273574687", "6070339397139", "PPS2633011022617", "樱桃木静音床-1.5米-松木铺板", "6990.00", "5242.50", "3268.67", "3250.00", "1448.83", "1467.50"),
    ("1036273574687", "6070339397140", "PPS2633011022614", "樱桃木静音床-1.8米-榉木铺板", "8630.00", "6472.50", "4035.92", "4020.00", "1788.58", "1804.50"),
    ("1036273574687", "6070339397141", "PPS2633011022618", "樱桃木静音床-1.8米-松木铺板", "7240.00", "5430.00", "3384.28", "3370.00", "1502.72", "1517.00"),
    ("1074244132390", "6287431318345", "PPS2633010022511", "樱桃木齐边床-1.2米-榉木铺板", "7450.00", "5587.50", "3478.88", "3470.00", "1549.62", "1558.50"),
    ("1074244132390", "6287431318346", "PPS2633010022515", "樱桃木齐边床-1.2米-松木铺板", "6420.00", "4815.00", "3005.92", "2990.00", "1327.08", "1343.00"),
    ("1074244132390", "6287431318347", "PPS2633010022512", "樱桃木齐边床-1.35米-榉木铺板", "7580.00", "5685.00", "3541.94", "3530.00", "1574.06", "1586.00"),
    ("1074244132390", "6287431318348", "PPS2633010022516", "樱桃木齐边床-1.35米-松木铺板", "6600.00", "4950.00", "3090.00", "3070.00", "1365.00", "1385.00"),
    ("1074244132390", "6287431318349", "PPS2633010022513", "樱桃木齐边床-1.5米-榉木铺板", "7730.00", "5797.50", "3615.51", "3600.00", "1601.99", "1617.50"),
    ("1074244132390", "6287431318350", "PPS2633010022517", "樱桃木齐边床-1.5米-松木铺板", "6700.00", "5025.00", "3132.04", "3120.00", "1389.96", "1402.00"),
    ("1074244132390", "6287431318351", "PPS2633010022514", "樱桃木齐边床-1.8米-榉木铺板", "7990.00", "5992.50", "3731.12", "3720.00", "1661.38", "1672.50"),
    ("1074244132390", "6287431318352", "PPS2633010022518", "樱桃木齐边床-1.8米-松木铺板", "6960.00", "5220.00", "3258.17", "3240.00", "1439.83", "1458.00"),
    ("1074244132390", "6287431318353", "PPS2633010022519", "樱桃木齐边床（软包款）-1.2米-榉木铺板", "8040.00", "6030.00", "3762.65", "3740.00", "1664.35", "1687.00"),
    ("1074244132390", "6287431318355", "PPS2633010022520", "樱桃木齐边床（软包款）-1.35米-榉木铺板", "8240.00", "6180.00", "3857.25", "3840.00", "1704.75", "1722.00"),
    ("1074244132390", "6287431318357", "PPS2633010022521", "樱桃木齐边床（软包款）-1.5米-榉木铺板", "8480.00", "6360.00", "3962.35", "3940.00", "1761.65", "1784.00"),
    ("1074244132390", "6287431318359", "PPS2633010022522", "樱桃木齐边床（软包款）-1.8米-榉木铺板", "8810.00", "6607.50", "4120.00", "4100.00", "1826.50", "1846.50"),
)
_FIELDS = (
    "item_id", "sku_id", "sku_code", "sku_name", "list_price",
    "signup_price", "mid_buyer_price", "small_promo", "current_deduct",
    "target_deduct",
)


def manifest_rows() -> list[dict[str, str]]:
    return [dict(zip(_FIELDS, row, strict=True)) for row in MANIFEST_ROWS]


def _canonical_sha(rows: list[dict[str, str]]) -> str:
    payload = [[row[key] for key in _FIELDS if key != "sku_name"] for row in rows]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _scope(kind: str) -> list[dict[str, str]]:
    key = "current_deduct" if kind == "current" else "target_deduct"
    return [{"item_id": row["item_id"], "sku_id": row["sku_id"],
             "expected_deduct": row[key]} for row in manifest_rows()]


def _scope_sha(kind: str) -> str:
    payload = [[row["item_id"], row["sku_id"], row["expected_deduct"]]
               for row in _scope(kind)]
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()


def request_payload() -> dict:
    return {
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "activity_ids": list(ACTIVITY_IDS),
        "target_activity_id": TARGET_ACTIVITY_ID,
        "manifest_sha256": MANIFEST_SHA256,
        "current_scope_sha256": CURRENT_SCOPE_SHA256,
        "target_scope_sha256": TARGET_SCOPE_SHA256,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_snapshot_artifact_sha256": SOURCE_SNAPSHOT_ARTIFACT_SHA256,
        "start_at": START_AT,
        "end_at": END_AT,
    }


def validate_request(payload: dict) -> bool:
    return isinstance(payload, dict) and payload == request_payload()


def _boundary(*, platform_read=False, platform_write=False) -> dict:
    return {
        "plan7_only": True, "platform_read": platform_read,
        "platform_write": platform_write, "account_action": False,
        "price_change": False, "signup_price_change": False,
        "activity_create": False, "add_remove_item": False,
        "enable_disable_delete": False, "notification": False,
        "automatic_retry": False,
    }


def _fail(error: str, **extra) -> dict:
    return {"ok": False, "error": error,
            "execution_boundary": _boundary(
                platform_read=bool(extra.pop("platform_read", False)),
                platform_write=extra.pop("platform_write", False)), **extra}


def _money(value) -> str:
    if value in (None, ""):
        return ""
    return f"{Decimal(str(value)):.2f}"


def _get_plan(db: Session, *, lock=False) -> CampaignPlan | None:
    stmt = select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID, CampaignPlan.workflow_key == WORKFLOW_KEY)
    return db.execute(stmt.with_for_update() if lock else stmt).scalar_one_or_none()


def _validate_plan(plan: CampaignPlan | None) -> dict | None:
    if plan is None:
        return _fail("workflow_not_found")
    if (plan.status != "alarmed" or plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or str(plan.qn_campaign_title or "").strip() != "超级立减"
            or not plan.start_at or not plan.end_at
            or plan.start_at.strftime("%Y-%m-%d %H:%M:%S") != START_AT
            or plan.end_at.strftime("%Y-%m-%d %H:%M:%S") != END_AT):
        return _fail("plan7_small_promo_plan_identity_drift")
    return None


def _validate_source_snapshot(db: Session) -> dict | None:
    snapshot = db.get(CampaignEvidenceSnapshot, SOURCE_SNAPSHOT_ID)
    if (snapshot is None or snapshot.plan_id != PLAN_ID
            or snapshot.workflow_key != WORKFLOW_KEY
            or snapshot.request_id != SOURCE_SNAPSHOT_REQUEST_ID
            or snapshot.artifact_sha256 != SOURCE_SNAPSHOT_ARTIFACT_SHA256):
        return _fail("plan7_small_promo_source_snapshot_drift")
    expected = {(r["item_id"], r["sku_id"]): r for r in _scope("current")}
    actual = {}
    for row in snapshot.rows or []:
        key = (str(row.get("item_id") or ""), str(row.get("sku_id") or ""))
        if key in expected:
            actual[key] = {
                "expected_deduct": _money(row.get("actual_deduct")),
                "activity_ids": [str(v) for v in row.get("activity_ids") or []],
            }
    if len(actual) != 20 or any(
            actual[key]["expected_deduct"] != expected[key]["expected_deduct"]
            or actual[key]["activity_ids"] != [TARGET_ACTIVITY_ID]
            for key in expected):
        return _fail("plan7_small_promo_source_snapshot_scope_drift")
    return None


def _validate_erp_rows(db: Session) -> dict | None:
    rows = db.execute(select(PricingSku, PricingSkuPromo).join(
        PricingSkuPromo, PricingSkuPromo.sku_code == PricingSku.sku_code
    ).where(PricingSku.sku_code.in_([r["sku_code"] for r in manifest_rows()]))).all()
    actual = []
    for sku, promo in rows:
        actual.append({
            "item_id": str(promo.taobao_item_id or ""),
            "sku_id": str(promo.taobao_sku_id or ""),
            "sku_code": str(sku.sku_code or ""), "sku_name": str(sku.sku or ""),
            "list_price": _money(sku.list_price), "signup_price": _money(sku.daily_price),
            "mid_buyer_price": _money(promo.mid_buyer_price),
            "small_promo": _money(sku.small_promo),
        })
    expected = [{key: row[key] for key in _FIELDS[:8]} for row in manifest_rows()]
    if sorted(actual, key=lambda r: (r["item_id"], r["sku_id"])) != expected:
        return _fail("plan7_small_promo_erp_manifest_drift", actual_rows=actual)
    forbidden = db.execute(select(PricingSku, PricingSkuPromo).join(
        PricingSkuPromo, PricingSkuPromo.sku_code == PricingSku.sku_code
    ).where(PricingSkuPromo.taobao_sku_id == FORBIDDEN_SKU_ID)).one_or_none()
    if not forbidden:
        return _fail("plan7_small_promo_forbidden_accessory_missing")
    sku, promo = forbidden
    if (sku.sku_code != FORBIDDEN_SKU_CODE or _money(sku.small_promo) != "140.00"
            or _money(promo.mid_buyer_price) != "136.63"
            or Decimal(str(promo.mid_buyer_price)) >= Decimal(str(sku.small_promo))):
        return _fail("plan7_small_promo_forbidden_accessory_identity_drift")
    return None


def _validate_readback(result: dict, *, kind: str) -> dict | None:
    if not isinstance(result, dict) or not result.get("ok"):
        return _fail(str((result or {}).get("error") or "plan7_small_promo_readback_failed"),
                     platform_read=True)
    if (result.get("execution_boundary") or {}).get("platform_write") is not False:
        return _fail("plan7_small_promo_readback_boundary_violation", platform_read=True)
    expected = {(r["item_id"], r["sku_id"]): r for r in _scope(kind)}
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    if len(rows) != 20:
        return _fail("plan7_small_promo_readback_count_drift", platform_read=True)
    for row in rows:
        key = (str(row.get("item_id") or ""), str(row.get("sku_id") or ""))
        if (key not in expected or _money(row.get("actual_deduct"))
                != expected[key]["expected_deduct"]
                or row.get("classification") != "correct_effective"
                or str(row.get("status") or "") not in {"进行中", "生效中"}
                or [str(v) for v in row.get("activity_ids") or []]
                != [TARGET_ACTIVITY_ID]):
            return _fail("plan7_small_promo_platform_state_drift",
                         platform_read=True, row=row)
    activity_rows = result.get("activity_rows") or []
    by_id = {str(row.get("activity_id") or ""): row for row in activity_rows}
    if set(by_id) != set(ACTIVITY_IDS) or any(
            not by_id[activity_id].get("identity_readable")
            or str(by_id[activity_id].get("status") or "") not in {"进行中", "生效中"}
            or START_AT not in str(by_id[activity_id].get("row_text") or "")
            or END_AT not in str(by_id[activity_id].get("row_text") or "")
            for activity_id in ACTIVITY_IDS):
        return _fail("plan7_small_promo_activity_identity_drift", platform_read=True)
    return None


def _platform_read(db: Session, kind: str) -> dict:
    scope = _scope(kind)
    digest = CURRENT_SCOPE_SHA256 if kind == "current" else TARGET_SCOPE_SHA256
    return web_agent_service.audit_plan7_single_discount(
        db, workflow_key=WORKFLOW_KEY, scope=scope, scope_sha256=digest,
        start_at=START_AT, end_at=END_AT)


def _existing_attempt(db: Session) -> CampaignExecutionAttempt | None:
    return db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
        CampaignExecutionAttempt.scope_sha256 == TARGET_SCOPE_SHA256,
    ).with_for_update()).scalar_one_or_none()


def _terminal_exact(result: dict) -> bool:
    terminal = result.get("official_terminal") or {}
    boundary = result.get("execution_boundary") or {}
    return (result.get("ok") is True and result.get("submitted") is True
            and result.get("activity_id") == TARGET_ACTIVITY_ID
            and terminal.get("state") == "complete" and terminal.get("ok") == 20
            and terminal.get("failed") == 0 and terminal.get("source")
            == "exact_activity_editor_readback"
            and boundary.get("platform_write") is True)


def _persist(db: Session, plan: CampaignPlan, *, evidence_type: str,
             request_id: str, result: dict, scope_sha256: str):
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    return campaign_discount_audit_service._persist(
        db, plan=plan, evidence_type=evidence_type, request_id=request_id,
        web_agent_job_id=result.get("web_agent_job_id"), scope_digest=scope_sha256,
        status="complete", summary=result.get("platform_summary") or result.get("official_terminal"),
        rows=result.get("rows") or result.get("verified_rows") or [], failure_rows=[],
        boundary=result.get("execution_boundary") or _boundary(platform_read=True),
        artifact={"kind": "canonical_json", "filename": f"{evidence_type}.json",
                  "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                  "content_b64": __import__("base64").b64encode(payload).decode("ascii")})


def execute(db: Session, *, payload: dict) -> dict:
    if (_canonical_sha(manifest_rows()) != MANIFEST_SHA256
            or _scope_sha("current") != CURRENT_SCOPE_SHA256
            or _scope_sha("target") != TARGET_SCOPE_SHA256
            or not validate_request(payload)):
        return _fail("plan7_small_promo_request_not_allowed")
    plan = _get_plan(db)
    for error in (_validate_plan(plan), _validate_source_snapshot(db), _validate_erp_rows(db)):
        if error:
            return error
    existing = _existing_attempt(db)
    if existing:
        return _fail("plan7_small_promo_attempt_already_consumed_no_retry",
                     attempt_id=existing.id, attempt_state=existing.state,
                     platform_write=existing.platform_write_observed)

    db.commit()
    pre_read = _platform_read(db, "current")
    pre_error = _validate_readback(pre_read, kind="current")
    if pre_error:
        # A manually completed exact target is a safe no-write terminal.
        target_read = _platform_read(db, "target")
        if _validate_readback(target_read, kind="target") is not None:
            return pre_error
        attempt = CampaignExecutionAttempt(
            id=secrets.token_hex(12), plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
            operation=OPERATION, scope_sha256=TARGET_SCOPE_SHA256,
            state="completed", write_claimed=False, platform_write_observed=False,
            automatic_retry_allowed=False, last_step="already_exact_readback",
            request_id=f"plan7-small-promo-noop-{secrets.token_hex(8)}",
            web_agent_job_id=target_read.get("web_agent_job_id"),
            result_summary={"already_exact": True})
        db.add(attempt)
        db.commit()
        return {"ok": True, "already_exact_no_write": True,
                "attempt_id": attempt.id,
                "execution_boundary": _boundary(platform_read=True)}

    plan = _get_plan(db, lock=True)
    for error in (_validate_plan(plan), _validate_source_snapshot(db), _validate_erp_rows(db)):
        if error:
            return error
    if _existing_attempt(db):
        return _fail("plan7_small_promo_claim_raced_no_write")
    attempt_id = secrets.token_hex(12)
    attempt = CampaignExecutionAttempt(
        id=attempt_id, plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=OPERATION, scope_sha256=TARGET_SCOPE_SHA256,
        state="write_claimed", write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc), platform_write_observed=False,
        automatic_retry_allowed=False,
        request_id=f"plan7-small-promo-{secrets.token_hex(8)}",
        web_agent_job_id=pre_read.get("web_agent_job_id"),
        last_step="fresh_current_readback_then_write_claimed",
        result_summary={"trigger": request_payload(),
                        "pre_read_job_id": pre_read.get("web_agent_job_id")})
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("plan7_small_promo_claim_raced_no_write")

    web_payload = {**payload, "rows": manifest_rows()}
    try:
        terminal = web_agent_service.correct_plan7_small_promo(db, payload=web_payload)
    except Exception as exc:  # outcome is unknown after a durable claim
        terminal = {"ok": False, "submitted": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "execution_boundary": {"platform_write": None}}
    observed = (terminal.get("execution_boundary") or {}).get("platform_write")
    if observed not in {True, False}:
        observed = None
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.platform_write_observed = observed
    attempt.web_agent_job_id = terminal.get("web_agent_job_id")
    attempt.state = "platform_terminal" if _terminal_exact(terminal) else (
        "failed" if observed is False else "unknown")
    attempt.last_step = "official_terminal" if _terminal_exact(terminal) else "platform_outcome_not_exact"
    attempt.error_code = None if _terminal_exact(terminal) else str(
        terminal.get("error") or "plan7_small_promo_terminal_not_exact")[:128]
    attempt.result_summary = {**(attempt.result_summary or {}),
                              "platform_submit": terminal.get("platform_submit"),
                              "official_terminal": terminal.get("official_terminal"),
                              "terminal_error": terminal.get("error"),
                              "write_steps": terminal.get("write_steps"),
                              "partial_success_item_ids": terminal.get(
                                  "partial_success_item_ids"),
                              "uncertain_item_ids": terminal.get(
                                  "uncertain_item_ids")}
    db.commit()
    if not _terminal_exact(terminal):
        return _fail("plan7_small_promo_terminal_not_exact_no_retry",
                     platform_read=True, platform_write=observed,
                     attempt_id=attempt_id, terminal=terminal)
    terminal_snapshot = _persist(
        db, plan, evidence_type="plan7_small_promo_platform_terminal",
        request_id=f"plan7-small-promo-terminal-{secrets.token_hex(6)}",
        result=terminal, scope_sha256=TARGET_SCOPE_SHA256)

    post_read = _platform_read(db, "target")
    post_error = _validate_readback(post_read, kind="target")
    readback_snapshot = None
    if post_error is None:
        readback_snapshot = _persist(
            db, plan, evidence_type="plan7_small_promo_readback",
            request_id=f"plan7-small-promo-readback-{secrets.token_hex(6)}",
            result=post_read, scope_sha256=TARGET_SCOPE_SHA256)
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.state = "completed" if post_error is None else "failed"
    attempt.last_step = "readback_verified" if post_error is None else "post_submit_readback_failed"
    attempt.error_code = None if post_error is None else post_error["error"][:128]
    attempt.result_summary = {**(attempt.result_summary or {}),
                              "terminal_snapshot_id": terminal_snapshot.id,
                              "readback_snapshot_id": getattr(readback_snapshot, "id", None),
                              "readback_job_id": post_read.get("web_agent_job_id"),
                              "readback_error": post_error.get("error") if post_error else None}
    db.commit()
    if post_error:
        return {**post_error, "attempt_id": attempt_id, "submitted": True,
                "execution_boundary": _boundary(platform_read=True, platform_write=True)}
    return {"ok": True, "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
            "attempt_id": attempt_id, "activity_id": TARGET_ACTIVITY_ID,
            "sku_count": 20, "item_count": 2,
            "terminal_snapshot_id": terminal_snapshot.id,
            "readback_snapshot_id": readback_snapshot.id,
            "execution_boundary": _boundary(platform_read=True, platform_write=True)}

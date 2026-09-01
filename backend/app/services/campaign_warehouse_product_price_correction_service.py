"""Retired warehouse price-correction evidence and fail-closed entry.

The historical manifests remain for audit only.  The current user rule is that
warehouse item 1038725569412 / SKU 6060112621275 must not be repriced.  Every
invocation stops before database claims, Web-Agent calls, or platform access.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import campaign_discount_audit_service, web_agent_service


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
OPERATION = "warehouse_sku_price_correct"
ITEM_ID = "1038725569412"
SKU_ID = "6060112621275"
TITLE = "畔色实木岩板餐边柜樱桃木收纳储物柜北欧酒柜置物柜餐桌一体靠墙"
EXPECTED_PRODUCT_STATE = "仓库中"
OLD_PRICE = "1500.00"
TARGET_PRICE = "1420.00"
EXPECTED_QUANTITY = "6"
TOTAL_STOCK = "13006"
HEADERS = (
    "颜色分类", "放我家素材", "skuId", "cspuId", "价格", "数量",
    "商家编码", "是否上架",
)
ROWS = (
    ("洞石餐边柜整柜1.5米（全景组合）", "", "6221627251186", "0", "20152.00", "1000", "", "上架"),
    ("洞石餐边柜整柜1.8米（全景组合）", "", "6221627251187", "0", "21883.00", "1000", "", "上架"),
    ("洞石餐边柜整柜2.1米（全景组合）", "", "6221627251188", "0", "23012.00", "1000", "", "上架"),
    ("洞石餐边柜整柜1.5米（多抽组合）", "", "6221627251189", "0", "20636.00", "1000", "", "上架"),
    ("洞石餐边柜整柜1.8米（多抽组合）", "", "6221627251190", "0", "22132.00", "1000", "", "上架"),
    ("洞石餐边柜整柜2.1米（多抽组合）", "", "6221627251191", "0", "23261.00", "1000", "", "上架"),
    ("洞石餐边柜整柜1.5米（视界组合）", "", "6221627251192", "0", "21135.00", "1000", "", "上架"),
    ("洞石餐边柜整柜1.8米（视界组合）", "", "6221627251193", "0", "22381.00", "1000", "", "上架"),
    ("洞石餐边柜整柜2.1米（视界组合）", "", "6221627251194", "0", "23496.00", "1000", "", "上架"),
    ("榉木款洞石整柜1.5米", "", "6221627251195", "0", "20328.00", "1000", "", "上架"),
    ("榉木款洞石整柜1.8米", "", "6221627251196", "0", "20621.00", "1000", "", "上架"),
    ("榉木款洞石整柜2.1米", "", "6221627251197", "0", "21105.00", "1000", "", "上架"),
    ("黑胡桃定制", "", "6221627251198", "0", "1500.00", "1000", "", "上架"),
    ("尺寸微定制", "", SKU_ID, "0", OLD_PRICE, EXPECTED_QUANTITY, "", "上架"),
)
BASELINE_SHA256 = "cf079f0b2903fecf0e7267ce9331c91b31d7ee12cce06aff20508c98296ecde5"
TARGET_SHA256 = "4ad0097702e2126ff3b56a6bbaaf9e5d62c8849b5e4a5985e28d7b49e0266987"
USER_RULE_ERROR = "user_rule_excluded"


def manifest(*, target: bool = False) -> dict:
    rows = [list(row) for row in ROWS]
    if target:
        rows[-1][4] = TARGET_PRICE
    return {"item_id": ITEM_ID, "title": TITLE,
            "headers": list(HEADERS), "rows": rows}


def manifest_sha256(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def request_payload() -> dict:
    return {
        "item_id": ITEM_ID, "sku_id": SKU_ID, "expected_title": TITLE,
        "expected_product_state": EXPECTED_PRODUCT_STATE,
        "expected_old_price": OLD_PRICE, "target_price": TARGET_PRICE,
        "expected_quantity": EXPECTED_QUANTITY,
        "baseline_sha256": BASELINE_SHA256, "target_sha256": TARGET_SHA256,
    }


def validate_request(payload: dict) -> bool:
    """No request is executable; historical payloads are evidence only."""
    return False


def _boundary(*, platform_read=False, platform_write=False) -> dict:
    return {
        "exact_item_only": True, "exact_sku_only": True,
        "platform_read": platform_read, "platform_product_write": platform_write,
        "account_action": platform_write,
        "price_change": platform_write, "stock_change": False,
        "title_change": False, "merchant_code_change": False,
        "sku_state_change": False, "product_state_change": False,
        "publish_now": False, "activity_change": False,
        "notification": False, "automatic_retry": False,
    }


def _fail(error: str, **extra) -> dict:
    platform_write = extra.pop("platform_write", False)
    return {"ok": False, "error": error,
            "execution_boundary": _boundary(
                platform_read=bool(extra.pop("platform_read", False)),
                platform_write=platform_write),
            **extra}


def user_rule_excluded_result() -> dict:
    """Return the permanent user-rule tombstone without touching any system."""
    return {
        **_fail(USER_RULE_ERROR),
        "reason": "warehouse_item_no_signup_no_price_change",
        "item_id": ITEM_ID,
        "sku_id": SKU_ID,
        "claim_created": False,
        "web_agent_called": False,
        "platform_write": False,
        "price_change": False,
    }


def _get_plan(db: Session, *, lock=False) -> CampaignPlan | None:
    stmt = select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID, CampaignPlan.workflow_key == WORKFLOW_KEY)
    return db.execute(stmt.with_for_update() if lock else stmt).scalar_one_or_none()


def _validate_plan(plan: CampaignPlan | None) -> dict | None:
    if plan is None:
        return _fail("workflow_not_found")
    if (plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or str(plan.qn_campaign_title or "").strip() != "超级立减"):
        return _fail("warehouse_product_price_plan_identity_drift")
    return None


def _validate_readback(result: dict, *, target: bool) -> dict | None:
    if not isinstance(result, dict) or not result.get("ok"):
        return _fail(str((result or {}).get("error")
                         or "warehouse_product_price_readback_failed"),
                     platform_read=True)
    if result.get("platform_product_write") is not False:
        return _fail("warehouse_product_price_readback_boundary_violation",
                     platform_read=True)
    expected_sha = TARGET_SHA256 if target else BASELINE_SHA256
    expected_manifest = manifest(target=target)
    actual = result.get("manifest") or {}
    actual_core = {key: actual.get(key)
                   for key in ("item_id", "title", "headers", "rows")}
    state = result.get("warehouse_state") or {}
    expected_price = TARGET_PRICE if target else OLD_PRICE
    if (result.get("manifest_sha256") != expected_sha
            or actual.get("sha256") != expected_sha
            or actual_core != expected_manifest
            or state.get("item_id") != ITEM_ID
            or state.get("title") != TITLE
            or state.get("product_state") != EXPECTED_PRODUCT_STATE
            or state.get("min_price") != expected_price
            or state.get("total_stock") != TOTAL_STOCK):
        return _fail("warehouse_product_price_readback_drift",
                     platform_read=True, result=result)
    return None


def _existing_attempt(db: Session) -> CampaignExecutionAttempt | None:
    return db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == WORKFLOW_KEY,
        CampaignExecutionAttempt.operation == OPERATION,
        CampaignExecutionAttempt.scope_sha256 == TARGET_SHA256,
    ).with_for_update()).scalar_one_or_none()


def _terminal_exact(result: dict) -> bool:
    terminal = result.get("official_terminal") or {}
    boundary = result.get("execution_boundary") or {}
    readback = result.get("readback") or {}
    before = result.get("before") or {}
    staged = result.get("staged") or {}
    before_state = result.get("before_state") or {}
    state = result.get("after_state") or {}
    return bool(
        result.get("ok") is True and result.get("submitted") is True
        and result.get("item_id") == ITEM_ID and result.get("sku_id") == SKU_ID
        and terminal == {"state": "complete", "price": TARGET_PRICE,
                         "product_state": EXPECTED_PRODUCT_STATE,
                         "source": "fresh_editor_and_in_stock_readback"}
        and before.get("sha256") == BASELINE_SHA256
        and {key: before.get(key) for key in ("item_id", "title", "headers", "rows")}
        == manifest()
        and staged.get("sha256") == TARGET_SHA256
        and {key: staged.get(key) for key in ("item_id", "title", "headers", "rows")}
        == manifest(target=True)
        and readback.get("sha256") == TARGET_SHA256
        and {key: readback.get(key) for key in ("item_id", "title", "headers", "rows")}
        == manifest(target=True)
        and before_state.get("product_state") == EXPECTED_PRODUCT_STATE
        and before_state.get("min_price") == OLD_PRICE
        and before_state.get("total_stock") == TOTAL_STOCK
        and state.get("product_state") == EXPECTED_PRODUCT_STATE
        and state.get("min_price") == TARGET_PRICE
        and state.get("total_stock") == TOTAL_STOCK
        and boundary.get("platform_product_write") is True
        and boundary.get("account_action") is True
        and boundary.get("price_change") is True
        and boundary.get("stock_change") is False
        and boundary.get("title_change") is False
        and boundary.get("merchant_code_change") is False
        and boundary.get("sku_state_change") is False
        and boundary.get("product_state_change") is False
        and boundary.get("publish_now") is False)


def _persist(db: Session, plan: CampaignPlan, *, evidence_type: str,
             request_id: str, result: dict, scope_sha256: str):
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    rows = ((result.get("manifest") or result.get("readback") or {}).get("rows")
            or [])
    return campaign_discount_audit_service._persist(
        db, plan=plan, evidence_type=evidence_type, request_id=request_id,
        web_agent_job_id=result.get("web_agent_job_id"),
        scope_digest=scope_sha256, status="complete",
        summary=result.get("official_terminal") or result.get("warehouse_state"),
        rows=rows, failure_rows=[],
        boundary=result.get("execution_boundary") or _boundary(platform_read=True),
        artifact={"kind": "canonical_json", "filename": f"{evidence_type}.json",
                  "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                  "content_b64": base64.b64encode(payload).decode("ascii")})


def execute(db: Session, *, payload: dict) -> dict:
    # Current explicit user rule supersedes the historical one-shot plan.  This
    # unconditional first instruction is intentionally before validation,
    # database reads/claims, evidence persistence, and every Web-Agent call.
    return user_rule_excluded_result()

    # Historical implementation below is intentionally unreachable and kept
    # only as an auditable record of the retired safety design.
    if not validate_request(payload):
        return _fail("warehouse_product_price_request_not_allowed")
    plan = _get_plan(db)
    error = _validate_plan(plan)
    if error:
        return error
    existing = _existing_attempt(db)
    if existing:
        return _fail("warehouse_product_price_attempt_already_consumed_no_retry",
                     attempt_id=existing.id, attempt_state=existing.state,
                     platform_write=existing.platform_write_observed)

    db.commit()
    pre_read = web_agent_service.read_warehouse_product_price(db, target=False)
    pre_error = _validate_readback(pre_read, target=False)
    if pre_error:
        target_read = web_agent_service.read_warehouse_product_price(db, target=True)
        if _validate_readback(target_read, target=True) is not None:
            return pre_error
        attempt = CampaignExecutionAttempt(
            id=secrets.token_hex(12), plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
            operation=OPERATION, scope_sha256=TARGET_SHA256,
            state="completed", write_claimed=False, platform_write_observed=False,
            automatic_retry_allowed=False, last_step="already_exact_readback",
            request_id=f"warehouse-product-price-noop-{secrets.token_hex(6)}",
            web_agent_job_id=target_read.get("web_agent_job_id"),
            result_summary={"already_exact": True})
        db.add(attempt)
        db.commit()
        return {"ok": True, "already_exact_no_write": True,
                "attempt_id": attempt.id,
                "execution_boundary": _boundary(platform_read=True)}

    plan = _get_plan(db, lock=True)
    error = _validate_plan(plan)
    if error:
        return error
    if _existing_attempt(db):
        return _fail("warehouse_product_price_claim_raced_no_write")
    attempt_id = secrets.token_hex(12)
    attempt = CampaignExecutionAttempt(
        id=attempt_id, plan_id=PLAN_ID, workflow_key=WORKFLOW_KEY,
        operation=OPERATION, scope_sha256=TARGET_SHA256,
        state="write_claimed", write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc),
        platform_write_observed=False, automatic_retry_allowed=False,
        request_id=f"warehouse-product-price-{secrets.token_hex(6)}",
        web_agent_job_id=pre_read.get("web_agent_job_id"),
        last_step="fresh_baseline_then_write_claimed",
        result_summary={"trigger": request_payload(),
                        "pre_read_job_id": pre_read.get("web_agent_job_id")})
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _fail("warehouse_product_price_claim_raced_no_write")

    baseline_snapshot = _persist(
        db, plan, evidence_type="warehouse_product_price_baseline",
        request_id=f"warehouse-price-baseline-{secrets.token_hex(6)}",
        result=pre_read, scope_sha256=BASELINE_SHA256)
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.result_summary = {**(attempt.result_summary or {}),
                              "baseline_snapshot_id": baseline_snapshot.id}
    db.commit()

    try:
        terminal = web_agent_service.correct_warehouse_product_price(
            db, payload=payload)
    except Exception as exc:  # durable claim makes every transport error unknown
        terminal = {"ok": False, "submitted": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "execution_boundary": {"platform_product_write": None}}
    observed = (terminal.get("execution_boundary") or {}).get(
        "platform_product_write")
    if observed not in {True, False}:
        observed = None
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.platform_write_observed = observed
    attempt.web_agent_job_id = terminal.get("web_agent_job_id")
    attempt.state = "platform_terminal" if _terminal_exact(terminal) else (
        "failed" if observed is False else "unknown")
    attempt.last_step = ("platform_readback_exact" if _terminal_exact(terminal)
                         else "platform_outcome_not_exact")
    attempt.error_code = (None if _terminal_exact(terminal) else str(
        terminal.get("error") or "warehouse_product_price_terminal_not_exact")[:128])
    attempt.result_summary = {**(attempt.result_summary or {}),
                              "official_terminal": terminal.get("official_terminal"),
                              "terminal_error": terminal.get("error"),
                              "needs_user_interaction": terminal.get(
                                  "needs_user_interaction")}
    db.commit()
    if not _terminal_exact(terminal):
        return _fail("warehouse_product_price_terminal_not_exact_no_retry",
                     platform_read=True, platform_write=observed,
                     attempt_id=attempt_id, terminal=terminal)
    terminal_snapshot = _persist(
        db, plan, evidence_type="warehouse_product_price_platform_terminal",
        request_id=f"warehouse-price-terminal-{secrets.token_hex(6)}",
        result=terminal, scope_sha256=TARGET_SHA256)

    post_read = web_agent_service.read_warehouse_product_price(db, target=True)
    post_error = _validate_readback(post_read, target=True)
    readback_snapshot = None
    if post_error is None:
        readback_snapshot = _persist(
            db, plan, evidence_type="warehouse_product_price_readback",
            request_id=f"warehouse-price-readback-{secrets.token_hex(6)}",
            result=post_read, scope_sha256=TARGET_SHA256)
    attempt = db.get(CampaignExecutionAttempt, attempt_id)
    attempt.state = "completed" if post_error is None else "failed"
    attempt.last_step = ("independent_readback_verified" if post_error is None
                         else "independent_readback_failed")
    attempt.error_code = None if post_error is None else post_error["error"][:128]
    attempt.result_summary = {**(attempt.result_summary or {}),
                              "terminal_snapshot_id": terminal_snapshot.id,
                              "readback_snapshot_id": getattr(
                                  readback_snapshot, "id", None),
                              "readback_job_id": post_read.get("web_agent_job_id"),
                              "readback_error": (post_error.get("error")
                                                 if post_error else None)}
    db.commit()
    if post_error:
        return {**post_error, "attempt_id": attempt_id, "submitted": True,
                "execution_boundary": _boundary(
                    platform_read=True, platform_write=True)}
    return {"ok": True, "workflow_key": WORKFLOW_KEY, "plan_id": PLAN_ID,
            "attempt_id": attempt_id, "item_id": ITEM_ID, "sku_id": SKU_ID,
            "old_price": OLD_PRICE, "target_price": TARGET_PRICE,
            "product_state": EXPECTED_PRODUCT_STATE,
            "terminal_snapshot_id": terminal_snapshot.id,
            "readback_snapshot_id": readback_snapshot.id,
            "execution_boundary": _boundary(
                platform_read=True, platform_write=True)}

"""One-shot remaining-item signup for Super Reduce plan 7.

This service is deliberately narrower than normal campaign automation.  It is
the only path that may apply the current plan's explicit custom-placeholder
safe-price decision.  Real SKU prices remain the ERP daily prices, existing
accepted items and single-item discounts are never uploaded again, and every
platform batch is claimed before upload and followed by an exact per-SKU
readback.  A claimed, failed or unknown batch is never retried by this entry.
"""
from __future__ import annotations

from collections import defaultdict
import base64
from datetime import datetime, timezone
import hashlib
import json
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignEvidenceSnapshot, CampaignPlan
from app.services import campaign_service, settings_service


WORKFLOW_KEY = "campaign:super-reduce:2026-09-01"
PLAN_ID = 7
EXPECTED_STATUS = "alarmed"
ACCEPTED_ITEM_IDS = {"797294092429"}
OFFICIAL_EXEMPT_ITEM_IDS = {"805268708396"}
NO_VALID_ONSALE_ITEM_IDS = {"724042164333", "919649052479"}
PRICE_HOLD_ITEM_IDS = {"1007407909979", "719436834260"}
NO_SALES_ITEM_IDS = {
    "1035582527998", "1037237545657", "1038071596128",
    "1038088884522", "1046991099343", "1047735990678",
    "1047741358718", "1047741902625", "1047742974354",
    "1047744482178", "1049596868352", "717780219729",
    "720234422814", "722275846168", "722912832184",
    "792992319206", "793062930418", "793128577437",
    "793135173033", "793554793170", "797139954559",
    "919215369800", "928309307277",
}
WHOLE_ITEM_EXCLUSION_IDS = {"1001358847694"}
AUTHORIZED_PLACEHOLDER_ITEM_IDS = {
    "1036273574687", "1036279566778", "1036312802226",
    "1036471324464", "1037224597517", "1037239277197",
    "1038062316046", "1038087212258", "1044450741007",
    "1046992019256", "1046992283533", "1048684921443",
    "1074244132390", "717388593550", "717418169535",
    "717434309002", "717809819543", "793052650673",
    "793084818113", "793178436895", "793202812082",
    "837902729785", "840301943626", "840643621692",
    "840659847455", "841201084787", "917179577721",
    "918340407291", "918692510350", "983187789816",
}
AUTHORIZED_FULL_SCOPE_SHA256 = (
    "d2ee3fd43a5c80d31799fc17b1b9f57c90db9f7ec7c78a0c88a501a7b51db2b8"
)
READONLY_QUALIFIED_ITEM_IDS = {
    "1036279566778", "1036312802226", "1036471324464",
    "1037224597517", "1037239277197", "1038062316046",
    "1038087212258", "1044450741007", "1046992019256",
    "1048684921443", "717388593550", "717434309002",
    "793052650673", "793178436895", "837902729785",
    "840301943626", "841201084787", "917179577721",
    "918340407291", "918692510350", "983187789816",
}
READONLY_HARD_STOP_ITEM_IDS = {
    "1046992283533", "717418169535", "840643621692", "840659847455",
}
AUTHORIZED_MISSING_ITEM_IDS = {
    "1036273574687", "1074244132390", "717809819543",
    "793084818113", "793202812082",
}
AUTHORIZED_ITEM_SCOPE_SHA256 = (
    "1f66d114e711b0fb3448a8a1503120bb5edd35a2d6416105f66545392f15bc86"
)
ATTEMPT_KEY = "campaign_plan7_remaining_signup_v1"
RECOVERY_INCIDENT_ID = "plan7-scope-review-08a753484e03"
MAX_ITEMS_PER_BATCH = 50
MAX_ROWS_PER_BATCH = 500
EXPECTED_ITEM_COUNT = 5
EXPECTED_ROW_COUNT = 70
EXPECTED_REAL_SKU_ROWS = 52
EXPECTED_PLACEHOLDER_SKU_ROWS = 18
PARTIAL_ATTEMPT_ID = "782299846f10d86ef4742c20"
PARTIAL_MANIFEST_SHA256 = (
    "2fa747d77823ed63baee82c5dbcc0d0fff6e248f77583dd4c9b074fa57d5c30d"
)
PARTIAL_TERMINAL_COUNTS = {"total_items": 5, "ok": 2, "failed": 3}
PARTIAL_AUDIT_EVIDENCE_TYPE = "plan7_remaining_partial_import_audit"
DRAFT_PUBLISH_ITEM_IDS = {"717809819543", "793084818113"}
DRAFT_PUBLISH_SKU_COUNT = 29
DRAFT_PUBLISH_SCOPE_SHA256 = (
    "0355c293c277330e490858df4f6b4bb57484881fcea9897f27c194b68fb7231b"
)
DRAFT_PUBLISH_RECEIPT_KEY = "campaign_plan7_partial_draft_publish_v1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execution_boundary(*, platform_write: bool = False) -> dict:
    return {
        "plan7_only": True,
        "platform_read": True,
        "platform_write": bool(platform_write),
        "account_action": bool(platform_write),
        "price_change": False,
        "real_sku_price_change": False,
        "placeholder_daily_price_change": False,
        "single_item_discount_write": False,
        "sku_rotation": False,
        "withdraw_pause_remove": False,
        "touches_plan8": False,
        "notification": False,
        "automatic_retry": False,
    }


def _fail(error: str, **detail) -> dict:
    return {
        "ok": False,
        "error": error,
        **detail,
        "execution_boundary": _execution_boundary(),
    }


def _load_attempt(db: Session) -> dict | None:
    raw = settings_service.get(db, ATTEMPT_KEY, env_fallback=False)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"status": "invalid"}
    return value if isinstance(value, dict) else {"status": "invalid"}


def _save_attempt(db: Session, payload: dict) -> None:
    settings_service.set_value(
        db,
        ATTEMPT_KEY,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        description="超级立减计划7剩余30品一次性分批报名回执（不含凭据）",
    )


def _canonical_scope_digest(item_ids: set[str]) -> str:
    raw = json.dumps(sorted(item_ids), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _manifest_rows(rows: list[dict]) -> list[dict]:
    return sorted(({
        "item_id": str(row.get("taobao_item_id") or ""),
        "sku_id": str(row.get("taobao_sku_id") or ""),
        "sku_code": str(row.get("sku_code") or ""),
        "signup_price": round(float(row.get("price")), 2),
        "is_custom_placeholder": bool(row.get("is_placeholder")),
        "price_source": (
            "authorized_placeholder_safe_cap"
            if row.get("is_placeholder") else "erp_daily_price"
        ),
    } for row in rows), key=lambda row: (row["item_id"], row["sku_id"]))


def _manifest_digest(rows: list[dict]) -> str:
    raw = json.dumps(
        _manifest_rows(rows), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_batches(rows: list[dict]) -> list[dict]:
    by_item: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_item[str(row["taobao_item_id"])].append(row)
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_items = 0
    for item_id in sorted(by_item):
        item_rows = sorted(
            by_item[item_id], key=lambda row: str(row["taobao_sku_id"]))
        if len(item_rows) > MAX_ROWS_PER_BATCH:
            raise ValueError("one_item_exceeds_batch_row_limit")
        if current and (
                current_items + 1 > MAX_ITEMS_PER_BATCH
                or len(current) + len(item_rows) > MAX_ROWS_PER_BATCH):
            batches.append(current)
            current = []
            current_items = 0
        current.extend(item_rows)
        current_items += 1
    if current:
        batches.append(current)
    return [{
        "batch_index": index,
        "item_ids": sorted({str(row["taobao_item_id"]) for row in batch}),
        "row_count": len(batch),
        "scope_sha256": _manifest_digest(batch),
        "rows": batch,
    } for index, batch in enumerate(batches, start=1)]


def _validate_price_sources(db: Session, rows: list[dict], stats: dict) -> dict:
    mapped = {}
    for sku, promo in campaign_service._mapped_pairs(db):
        item_id = str(promo.taobao_item_id or "").strip()
        for sku_id in campaign_service._expand_sku_ids(promo):
            mapped[(item_id, str(sku_id))] = sku
    lowered = {
        (str(row.get("taobao_item_id") or ""),
         str(row.get("taobao_sku_id") or "")): row
        for row in stats.get("placeholder_price_lowered") or []
    }
    problems = []
    placeholder_count = 0
    real_count = 0
    for row in rows:
        pair = (
            str(row.get("taobao_item_id") or ""),
            str(row.get("taobao_sku_id") or ""),
        )
        sku = mapped.get(pair)
        if sku is None:
            problems.append({"pair": pair, "error": "mapped_sku_missing"})
            continue
        actual = round(float(row["price"]), 2)
        if row.get("is_placeholder"):
            placeholder_count += 1
            proof = lowered.get(pair)
            if (not bool(getattr(sku, "is_custom_placeholder", False))
                    or proof is None
                    or proof.get("current_live_price") is None
                    or float(proof["current_live_price"]) <= actual
                    or round(float(proof["safe_cap"]), 2) != actual
                    or proof.get("authorization")
                    != "current_plan_user_decision"):
                problems.append({
                    "pair": pair,
                    "error": "placeholder_safe_price_proof_invalid",
                    "proof": proof,
                })
        else:
            real_count += 1
            daily = getattr(sku, "daily_price", None)
            if (bool(getattr(sku, "is_custom_placeholder", False))
                    or daily is None
                    or round(float(daily), 2) != actual):
                problems.append({
                    "pair": pair,
                    "error": "real_sku_signup_price_not_erp_daily_price",
                    "signup_price": actual,
                    "erp_daily_price": (
                        round(float(daily), 2) if daily is not None else None),
                })
    return {
        "ok": not problems,
        "real_sku_rows": real_count,
        "placeholder_sku_rows": placeholder_count,
        "problems": problems,
    }


def _active_or_pending_items(rows: list[dict], scope: set[str]) -> set[str]:
    allowed_statuses = {"已发布设定", "活动中", "暂停"}
    return {
        str(row.get("item_id") or "")
        for row in rows
        if str(row.get("item_id") or "") in scope
        and str(row.get("status") or "") in allowed_statuses
    } - {""}


def _classify_existing_scope(
        expected_rows: list[dict], live_rows: list[dict]) -> dict:
    """Split the reviewed 30 items into exact, conflicting and wholly missing."""
    by_item: dict[str, list[dict]] = defaultdict(list)
    for row in expected_rows:
        by_item[str(row.get("taobao_item_id") or "")].append(row)
    present = _active_or_pending_items(
        live_rows, AUTHORIZED_PLACEHOLDER_ITEM_IDS)
    qualified: set[str] = set()
    hard_stop: set[str] = set()
    missing: set[str] = set()
    verification: dict[str, dict] = {}
    for item_id, item_rows in sorted(by_item.items()):
        if item_id not in present:
            missing.add(item_id)
            verification[item_id] = {
                "ok": False,
                "classification": "wholly_missing",
                "checked_skus": len(item_rows),
                "verified_skus": 0,
                "failed_skus": len(item_rows),
            }
            continue
        checked = _verify_all_skus(item_rows, live_rows)
        checked["classification"] = (
            "qualified_existing" if checked["ok"] else "existing_price_conflict")
        verification[item_id] = checked
        (qualified if checked["ok"] else hard_stop).add(item_id)
    return {
        "qualified_item_ids": sorted(qualified),
        "hard_stop_item_ids": sorted(hard_stop),
        "missing_item_ids": sorted(missing),
        "verification": verification,
    }


def _verify_all_skus(expected_rows: list[dict], live_rows: list[dict]) -> dict:
    allowed_statuses = {"已发布设定", "活动中", "暂停"}
    live_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in live_rows:
        pair = (
            str(row.get("item_id") or ""),
            str(row.get("sku_id") or ""),
        )
        live_by_pair[pair].append(row)
    failures = []
    verified = []
    for expected in expected_rows:
        pair = (
            str(expected.get("taobao_item_id") or ""),
            str(expected.get("taobao_sku_id") or ""),
        )
        price = round(float(expected["price"]), 2)
        candidates = live_by_pair.get(pair, [])
        exact = [row for row in candidates if (
            str(row.get("status") or "") in allowed_statuses
            and row.get("activity_price") is not None
            and abs(float(row["activity_price"]) - price) <= 0.005
        )]
        if exact:
            verified.append({
                "item_id": pair[0],
                "sku_id": pair[1],
                "expected_activity_price": price,
                "actual_activity_prices": sorted({
                    round(float(row["activity_price"]), 2) for row in exact
                }),
                "statuses": sorted({str(row.get("status") or "") for row in exact}),
                "is_custom_placeholder": bool(expected.get("is_placeholder")),
            })
            continue
        failures.append({
            "item_id": pair[0],
            "sku_id": pair[1],
            "expected_activity_price": price,
            "actual_activity_prices": [
                row.get("activity_price") for row in candidates
            ],
            "actual_statuses": [str(row.get("status") or "") for row in candidates],
            "is_custom_placeholder": bool(expected.get("is_placeholder")),
            "error": "exact_activity_price_or_pending_status_missing",
        })
    return {
        "ok": not failures,
        "checked_skus": len(expected_rows),
        "verified_skus": len(verified),
        "failed_skus": len(failures),
        "verified": verified,
        "failures": failures,
    }


def _persist_snapshot(
        db: Session, *, request_id: str, scope_sha256: str,
        result_status: str, rows: list[dict], summary: dict,
        export_evidence: dict | None = None,
        failure_rows: list[dict] | None = None,
        platform_write: bool = False,
        evidence_type: str = "plan7_remaining_signup_batch",
) -> None:
    export = export_evidence or {}
    db.add(CampaignEvidenceSnapshot(
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        evidence_type=evidence_type,
        request_id=request_id,
        web_agent_job_id=str(summary.get("job_id") or "") or None,
        scope_sha256=scope_sha256,
        result_status=result_status,
        platform_summary=summary,
        rows=_manifest_rows(rows),
        failure_rows=failure_rows or [],
        execution_boundary=_execution_boundary(platform_write=platform_write),
        artifact_kind="campaign_enrolled_export" if export else None,
        artifact_filename=export.get("filename"),
        artifact_sha256=export.get("sha256"),
        artifact_size=export.get("size"),
    ))


def _attempt_for_response(attempt: dict) -> dict:
    return {
        key: value for key, value in attempt.items()
        if key != "manifest_rows"
    }


def execute_plan7_remaining_signup(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_status: str, expected_item_scope_sha256: str,
        recovery_incident_id: str,
) -> dict:
    """Read current evidence, claim once, submit bounded batches and read back."""
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_status != EXPECTED_STATUS
            or expected_item_scope_sha256 != AUTHORIZED_ITEM_SCOPE_SHA256
            or recovery_incident_id != RECOVERY_INCIDENT_ID):
        return _fail("remaining_signup_request_not_allowed")
    if _canonical_scope_digest(AUTHORIZED_PLACEHOLDER_ITEM_IDS) \
            != AUTHORIZED_FULL_SCOPE_SHA256:
        return _fail("remaining_signup_authorized_scope_constant_invalid")
    if _canonical_scope_digest(AUTHORIZED_MISSING_ITEM_IDS) \
            != AUTHORIZED_ITEM_SCOPE_SHA256:
        return _fail("remaining_signup_missing_scope_constant_invalid")
    partitions = (
        READONLY_QUALIFIED_ITEM_IDS,
        READONLY_HARD_STOP_ITEM_IDS,
        AUTHORIZED_MISSING_ITEM_IDS,
    )
    if (set().union(*partitions) != AUTHORIZED_PLACEHOLDER_ITEM_IDS
            or any(partitions[left] & partitions[right]
                   for left in range(len(partitions))
                   for right in range(left + 1, len(partitions)))):
        return _fail("remaining_signup_scope_partition_constant_invalid")

    plan = db.get(CampaignPlan, PLAN_ID)
    if plan is None or plan.workflow_key != WORKFLOW_KEY:
        return _fail("workflow_not_found")
    if (plan.campaign_type != "super_reduce"
            or plan.platform_activity_mode != "long_running_update"
            or str(plan.qn_campaign_title or "").strip() != "超级立减"):
        return _fail("remaining_signup_plan_identity_not_allowed")
    attempt = _load_attempt(db)
    if attempt:
        if attempt.get("status") == "completed":
            return {
                "ok": True,
                "idempotent_replay": True,
                "workflow_key": WORKFLOW_KEY,
                "plan_id": PLAN_ID,
                "plan_status": plan.status,
                "attempt": _attempt_for_response(attempt),
                "execution_boundary": _execution_boundary(platform_write=True),
            }
        return _fail(
            "remaining_signup_attempt_already_claimed_no_retry",
            attempt=_attempt_for_response(attempt),
            plan_status=plan.status,
        )
    if plan.status != expected_status:
        return _fail(
            "remaining_signup_status_cas_mismatch",
            expected_status=expected_status,
            actual_status=plan.status,
        )
    official = campaign_service.official_scope_for_plan(plan)
    if (not official.get("configured") or not official.get("all_store")
            or set(official.get("exempt_items") or set())
            != OFFICIAL_EXEMPT_ITEM_IDS):
        return _fail(
            "remaining_signup_official_scope_drift",
            actual_exempt_item_ids=sorted(official.get("exempt_items") or set()),
        )
    if not ACCEPTED_ITEM_IDS <= campaign_service.platform_qualified_items(plan):
        return _fail("remaining_signup_prior_acceptance_missing")

    # Read-only refresh occurs before the irreversible claim.  A login or
    # evidence failure leaves the one-shot budget untouched.
    refreshed = campaign_service.refresh_floor_evidence_from_current_activity(
        db, plan, allow_placeholder_safe_lowering=True)
    if not refreshed.get("ok"):
        failed = _fail(
            refreshed.get("error") or "remaining_signup_evidence_refresh_failed",
            step=refreshed.get("step"),
            job_id=refreshed.get("job_id"),
            detail=refreshed.get("detail"),
        )
        # A pre-claim failure is safe to recover, but it must not disappear when
        # the caller disconnects or the on-demand Web-Agent exits.  Persist only
        # the read-only terminal facts; never create ATTEMPT_KEY here.
        _persist_snapshot(
            db,
            request_id=(
                str(refreshed.get("request_id") or "").strip()
                or f"plan7-remaining-preclaim-{secrets.token_hex(8)}"
            ),
            scope_sha256=AUTHORIZED_ITEM_SCOPE_SHA256,
            result_status="preclaim_failed",
            rows=[],
            summary={
                "recovery_incident_id": recovery_incident_id,
                "error": failed.get("error"),
                "step": failed.get("step"),
                "job_id": failed.get("job_id"),
                "detail": failed.get("detail"),
                "attempt_claimed": False,
                "platform_write": False,
            },
            platform_write=False,
            evidence_type="plan7_remaining_preclaim",
        )
        db.commit()
        return failed
    live_rows = refreshed.get("rows") or []
    review_rows, _review_stats = campaign_service.build_signup_rows(
        db, plan, allow_placeholder_safe_lowering=True)
    review_rows = [
        row for row in review_rows
        if str(row.get("taobao_item_id") or "")
        in AUTHORIZED_PLACEHOLDER_ITEM_IDS
    ]
    scope_review = _classify_existing_scope(review_rows, live_rows)
    if (set(scope_review["qualified_item_ids"])
            != READONLY_QUALIFIED_ITEM_IDS
            or set(scope_review["hard_stop_item_ids"])
            != READONLY_HARD_STOP_ITEM_IDS
            or set(scope_review["missing_item_ids"])
            != AUTHORIZED_MISSING_ITEM_IDS):
        return _fail(
            "remaining_signup_live_scope_review_drift",
            scope_review=scope_review,
            export_evidence=refreshed.get("export_evidence"),
        )
    good_rows = [
        row for row in review_rows
        if str(row.get("taobao_item_id") or "")
        in READONLY_QUALIFIED_ITEM_IDS
    ]
    discount_rows, _discount_stats = campaign_service.build_discount_rows(db, plan)
    good_discount_rows = [
        row for row in discount_rows
        if str(row.get("taobao_item_id") or "")
        in READONLY_QUALIFIED_ITEM_IDS
    ]
    price_math = campaign_service._check_price_math(
        db, plan, good_rows, good_discount_rows)
    if price_math.get("level") != "pass":
        return _fail(
            "remaining_signup_existing_price_math_blocked",
            scope_review=scope_review,
            price_math=price_math,
            export_evidence=refreshed.get("export_evidence"),
        )
    hard_stop_failures = []
    for item_id in sorted(READONLY_HARD_STOP_ITEM_IDS):
        hard_stop_failures.extend(
            scope_review["verification"][item_id].get("failures") or [])
    _persist_snapshot(
        db,
        request_id=f"plan7-remaining-review-{secrets.token_hex(8)}",
        scope_sha256=AUTHORIZED_FULL_SCOPE_SHA256,
        result_status="reviewed",
        rows=review_rows,
        summary={
            "recovery_incident_id": recovery_incident_id,
            "scope_review": scope_review,
            "qualified_price_math": price_math,
            "attempt_claimed": False,
            "platform_write": False,
        },
        export_evidence=refreshed.get("export_evidence"),
        failure_rows=hard_stop_failures,
        platform_write=False,
        evidence_type="plan7_remaining_scope_review",
    )
    db.commit()

    # Lock and recompute after the network read so two callers cannot both
    # claim the same platform write.
    plan = db.execute(select(CampaignPlan).where(
        CampaignPlan.id == PLAN_ID,
        CampaignPlan.workflow_key == WORKFLOW_KEY,
    ).with_for_update()).scalar_one_or_none()
    if plan is None:
        return _fail("workflow_not_found")
    raced = _load_attempt(db)
    if raced:
        return _fail(
            "remaining_signup_attempt_raced_no_write",
            attempt=_attempt_for_response(raced),
        )
    if plan.status != expected_status:
        return _fail(
            "remaining_signup_status_changed_after_refresh",
            actual_status=plan.status,
        )

    base_rows, base_stats = campaign_service.build_signup_rows(db, plan)
    blocked_scope = {
        str(row.get("taobao_item_id") or "")
        for row in base_stats.get("placeholder_price_blocked_items") or []
    }
    if blocked_scope != AUTHORIZED_PLACEHOLDER_ITEM_IDS:
        return _fail(
            "remaining_signup_placeholder_scope_drift",
            expected_item_ids=sorted(AUTHORIZED_PLACEHOLDER_ITEM_IDS),
            actual_item_ids=sorted(blocked_scope),
        )
    if set(base_stats.get("excluded_official_exempt_items") or []) \
            != OFFICIAL_EXEMPT_ITEM_IDS:
        return _fail("remaining_signup_official_exclusion_missing")
    hold_ids = {
        str(row.get("taobao_item_id") or "")
        for row in base_stats.get("excluded_price_hold_items") or []
    }
    if hold_ids != PRICE_HOLD_ITEM_IDS:
        return _fail(
            "remaining_signup_real_price_hold_scope_drift",
            expected_item_ids=sorted(PRICE_HOLD_ITEM_IDS),
            actual_item_ids=sorted(hold_ids),
        )

    safe_rows, safe_stats = campaign_service.build_signup_rows(
        db, plan, allow_placeholder_safe_lowering=True)
    rows = [
        row for row in safe_rows
        if str(row.get("taobao_item_id") or "")
        in AUTHORIZED_MISSING_ITEM_IDS
    ]
    actual_items = {str(row["taobao_item_id"]) for row in rows}
    if actual_items != AUTHORIZED_MISSING_ITEM_IDS:
        return _fail(
            "remaining_signup_safe_scope_incomplete",
            missing_item_ids=sorted(AUTHORIZED_MISSING_ITEM_IDS - actual_items),
            unexpected_item_ids=sorted(actual_items - AUTHORIZED_MISSING_ITEM_IDS),
        )
    if any(
        str(row.get("taobao_item_id") or "") in (
            ACCEPTED_ITEM_IDS | OFFICIAL_EXEMPT_ITEM_IDS
            | NO_VALID_ONSALE_ITEM_IDS | PRICE_HOLD_ITEM_IDS
        ) for row in rows
    ):
        return _fail("remaining_signup_forbidden_item_in_manifest")
    no_sales = set(safe_stats.get("excluded_no_sales_items") or [])
    if no_sales != NO_SALES_ITEM_IDS or no_sales & actual_items:
        return _fail(
            "remaining_signup_no_sales_scope_drift",
            expected_no_sales_item_ids=sorted(NO_SALES_ITEM_IDS),
            excluded_no_sales_item_ids=sorted(no_sales),
        )
    whole_excluded = {
        str(row.get("item_id") or row.get("taobao_item_id") or "")
        for row in safe_stats.get("excluded_whole_items") or []
    } - {""}
    if not WHOLE_ITEM_EXCLUSION_IDS <= whole_excluded:
        return _fail(
            "remaining_signup_whole_item_exclusion_missing",
            expected_item_ids=sorted(WHOLE_ITEM_EXCLUSION_IDS),
            actual_item_ids=sorted(whole_excluded),
        )

    price_sources = _validate_price_sources(db, rows, safe_stats)
    if not price_sources["ok"]:
        return _fail(
            "remaining_signup_price_source_guard_failed",
            price_sources=price_sources,
        )
    if (len(actual_items) != EXPECTED_ITEM_COUNT
            or len(rows) != EXPECTED_ROW_COUNT
            or price_sources["real_sku_rows"] != EXPECTED_REAL_SKU_ROWS
            or price_sources["placeholder_sku_rows"]
            != EXPECTED_PLACEHOLDER_SKU_ROWS):
        return _fail(
            "remaining_signup_manifest_count_drift",
            expected={
                "item_count": EXPECTED_ITEM_COUNT,
                "row_count": EXPECTED_ROW_COUNT,
                "real_sku_rows": EXPECTED_REAL_SKU_ROWS,
                "placeholder_sku_rows": EXPECTED_PLACEHOLDER_SKU_ROWS,
            },
            actual={
                "item_count": len(actual_items),
                "row_count": len(rows),
                "real_sku_rows": price_sources["real_sku_rows"],
                "placeholder_sku_rows": price_sources["placeholder_sku_rows"],
            },
        )
    checks = campaign_service.preflight(
        db, plan,
        exact_item_scope=AUTHORIZED_MISSING_ITEM_IDS,
        allow_placeholder_safe_lowering=True,
    )
    blocking = [row for row in checks if row.get("level") == "error"]
    by_rule = {str(row.get("rule")): row for row in checks}
    if (blocking or by_rule.get("R16", {}).get("level") != "pass"
            or by_rule.get("R17", {}).get("level") != "pass"
            or int(by_rule.get("R17", {}).get("checked") or 0)
            != price_sources["real_sku_rows"]):
        return _fail(
            "remaining_signup_preflight_blocked",
            blocking=blocking,
            gate_results={"R16": by_rule.get("R16"), "R17": by_rule.get("R17")},
            price_sources=price_sources,
        )

    batches = _build_batches(rows)
    if not batches:
        return _fail("remaining_signup_empty_manifest")
    attempt_id = secrets.token_hex(12)
    manifest_sha = _manifest_digest(rows)
    claimed = {
        "status": "claimed",
        "attempt_id": attempt_id,
        "claimed_at": _utcnow(),
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "authorized_item_scope_sha256": AUTHORIZED_ITEM_SCOPE_SHA256,
        "reviewed_full_scope_sha256": AUTHORIZED_FULL_SCOPE_SHA256,
        "manifest_sha256": manifest_sha,
        "item_ids": sorted(actual_items),
        "item_count": len(actual_items),
        "row_count": len(rows),
        "real_sku_rows": price_sources["real_sku_rows"],
        "placeholder_sku_rows": price_sources["placeholder_sku_rows"],
        "manifest_rows": _manifest_rows(rows),
        "excluded": {
            "already_accepted": sorted(ACCEPTED_ITEM_IDS),
            "readonly_qualified": sorted(READONLY_QUALIFIED_ITEM_IDS),
            "readonly_hard_stop": sorted(READONLY_HARD_STOP_ITEM_IDS),
            "official_exempt": sorted(OFFICIAL_EXEMPT_ITEM_IDS),
            "no_sales": sorted(no_sales),
            "no_valid_onsale_sku": sorted(NO_VALID_ONSALE_ITEM_IDS),
            "real_sku_price_hold": sorted(PRICE_HOLD_ITEM_IDS),
            "whole_item_exclusion": sorted(WHOLE_ITEM_EXCLUSION_IDS),
        },
        "batch_count": len(batches),
        "batches": [{
            "batch_index": batch["batch_index"],
            "item_ids": batch["item_ids"],
            "row_count": batch["row_count"],
            "scope_sha256": batch["scope_sha256"],
            "status": "pending",
        } for batch in batches],
        "automatic_retry": False,
        "single_item_discount_write": False,
    }
    _save_attempt(db, claimed)
    _persist_snapshot(
        db,
        request_id=f"plan7-remaining-{attempt_id}-manifest",
        scope_sha256=manifest_sha,
        result_status="claimed",
        rows=rows,
        summary={
            "attempt_id": attempt_id,
            "item_count": len(actual_items),
            "row_count": len(rows),
            "batch_count": len(batches),
            "price_sources": price_sources,
            "pre_submit_export": refreshed.get("export_evidence"),
        },
    )
    db.commit()

    completed_items = set(ACCEPTED_ITEM_IDS) | set(READONLY_QUALIFIED_ITEM_IDS)
    for batch in batches:
        batch_index = batch["batch_index"]
        attempt = _load_attempt(db) or claimed
        attempt["status"] = "executing"
        attempt["current_batch"] = batch_index
        attempt["batches"][batch_index - 1].update({
            "status": "claimed",
            "claimed_at": _utcnow(),
        })
        _save_attempt(db, attempt)
        db.commit()

        submitted = None
        platform_write_observed = None
        result = None
        classification = None
        post_submit = None
        verification = None
        try:
            upload = campaign_service._build_super_signup_xlsx(batch["rows"])
            result = campaign_service._upload_and_wait(
                db, "super_reduce", "commit", upload,
                campaign_service._fmt_dt(plan.start_at),
                campaign_service._fmt_dt(plan.end_at),
                plan=plan,
                expected_rows=batch["row_count"],
                expected_items=len(batch["item_ids"]),
            )
            submitted = bool(result.get("submitted"))
            platform_write_observed = bool(
                result.get("platform_write_observed") or submitted)
            classification = campaign_service._classify_final_signup(
                db, plan, result, batch["rows"], completed_items)
            if submitted:
                post_submit = (
                    campaign_service.refresh_floor_evidence_from_current_activity(
                        db, plan, allow_placeholder_safe_lowering=True))
                if post_submit.get("ok"):
                    accepted = set(
                        (classification or {}).get("accepted_item_ids") or [])
                    accepted_rows = [
                        row for row in batch["rows"]
                        if str(row["taobao_item_id"]) in accepted
                    ]
                    verification = _verify_all_skus(
                        accepted_rows, post_submit.get("rows") or [])
            batch_ok = bool(
                (classification or {}).get("ok")
                and not (classification or {}).get("no_sales_item_ids")
                and not (classification or {}).get("hard_failed_item_ids")
                and post_submit and post_submit.get("ok")
                and verification and verification.get("ok")
                and set((classification or {}).get("accepted_item_ids") or [])
                == set(batch["item_ids"])
            )
        except Exception as exc:  # noqa: BLE001 - unknown write outcome is terminal
            db.rollback()
            batch_ok = False
            result = result or {}
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["unknown_outcome"] = True
            platform_write_observed = True

        attempt = _load_attempt(db) or attempt
        status = "completed" if batch_ok else (
            "failed_unknown_outcome" if submitted is None
            or (result or {}).get("unknown_outcome") else "failed_no_retry"
        )
        batch_receipt = {
            "status": status,
            "finished_at": _utcnow(),
            "submitted": submitted,
            "platform_write_observed": platform_write_observed,
            "published": bool((result or {}).get("published")),
            "operation_semantics": (result or {}).get("operation_semantics"),
            "stopped_before": (result or {}).get("stopped_before"),
            "job_id": (result or {}).get("job"),
            "terminal_validation": (result or {}).get("validation"),
            "terminal_classification": classification,
            "post_submit_export": (
                (post_submit or {}).get("export_evidence")
                if isinstance(post_submit, dict) else None),
            "post_submit_verification": verification,
            "error": (
                (result or {}).get("error")
                or (classification or {}).get("error")
                or ((post_submit or {}).get("error")
                    if isinstance(post_submit, dict) else None)
            ),
            "automatic_retry": False,
        }
        attempt["batches"][batch_index - 1].update(batch_receipt)
        attempt["status"] = "executing" if batch_ok else status
        _save_attempt(db, attempt)
        export_evidence = (
            (post_submit or {}).get("export_evidence")
            if isinstance(post_submit, dict) else None)
        _persist_snapshot(
            db,
            request_id=f"plan7-remaining-{attempt_id}-batch-{batch_index}",
            scope_sha256=batch["scope_sha256"],
            result_status=status,
            rows=batch["rows"],
            summary={
                "attempt_id": attempt_id,
                "batch_index": batch_index,
                "job_id": (result or {}).get("job"),
                "submitted": submitted,
                "platform_write_observed": platform_write_observed,
                "terminal_validation": (result or {}).get("validation"),
                "terminal_classification": classification,
                "post_submit_verification": verification,
                "automatic_retry": False,
            },
            export_evidence=export_evidence,
            failure_rows=(verification or {}).get("failures") or [],
            platform_write=bool(platform_write_observed),
        )
        db.commit()

        receipt_result = {
            "ok": batch_ok,
            "submitted": bool(submitted),
            "platform_write_observed": bool(platform_write_observed),
            "job": (result or {}).get("job"),
            "validation": (result or {}).get("validation"),
            "terminal_classification": classification or {},
            "post_submit_export_evidence": export_evidence or {},
            "post_submit_verification": verification,
            "error": batch_receipt.get("error"),
            "step": f"plan7_remaining_batch_{batch_index}",
        }
        campaign_service._record_signup_execution_receipt(
            db, plan, receipt_result)
        if not batch_ok:
            plan.status = "alarmed"
            db.commit()
            return {
                "ok": False,
                "error": "remaining_signup_batch_failed_no_retry",
                "workflow_key": WORKFLOW_KEY,
                "plan_id": PLAN_ID,
                "plan_status": plan.status,
                "attempt": _attempt_for_response(attempt),
                "failed_batch": batch_index,
                "execution_boundary": _execution_boundary(
                    platform_write=bool(platform_write_observed)),
            }
        completed_items.update(batch["item_ids"])

    # All batches have exact terminals and exact per-SKU readback.  Preserve the
    # old accepted item and record a single full-scope terminal digest.
    plan = db.get(CampaignPlan, PLAN_ID)
    campaign_service._set_plan_item_marker(
        plan, "platform_qualified_items", completed_items)
    campaign_service._set_plan_item_marker(plan, "platform_no_sales_items", set())
    campaign_service._set_plan_item_marker(
        plan, "platform_hard_failed_items", READONLY_HARD_STOP_ITEM_IDS)
    all_safe_rows, _ = campaign_service.build_signup_rows(
        db, plan, allow_placeholder_safe_lowering=True)
    accepted_rows = [
        row for row in all_safe_rows
        if str(row.get("taobao_item_id") or "") in completed_items
    ]
    campaign_service._record_terminal_platform_acceptance(
        plan, accepted_rows, completed_items)
    # Four already-enrolled items remain price-conflicted and are intentionally
    # untouched. Keep the plan alarmed even after the only five missing items
    # finish, so no downstream path can call the whole 30-item scope complete.
    plan.status = "alarmed"
    final_attempt = _load_attempt(db) or claimed
    final_attempt.update({
        "status": "completed",
        "completed_at": _utcnow(),
        "completed_item_ids": sorted(completed_items),
        "submitted_new_item_count": len(AUTHORIZED_MISSING_ITEM_IDS),
        "submitted_new_row_count": len(rows),
        "readonly_qualified_item_ids": sorted(READONLY_QUALIFIED_ITEM_IDS),
        "readonly_hard_stop_item_ids": sorted(READONLY_HARD_STOP_ITEM_IDS),
    })
    final_attempt.pop("current_batch", None)
    _save_attempt(db, final_attempt)
    db.commit()
    return {
        "ok": True,
        "workflow_key": WORKFLOW_KEY,
        "plan_id": PLAN_ID,
        "plan_status": plan.status,
        "attempt": _attempt_for_response(final_attempt),
        "manifest_sha256": manifest_sha,
        "item_count": len(AUTHORIZED_MISSING_ITEM_IDS),
        "row_count": len(rows),
        "real_sku_rows": price_sources["real_sku_rows"],
        "placeholder_sku_rows": price_sources["placeholder_sku_rows"],
        "execution_boundary": _execution_boundary(platform_write=True),
    }


def audit_plan7_partial_signup(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_attempt_id: str, expected_manifest_sha256: str,
) -> dict:
    """Download the official failure workbook and re-export enrolled rows.

    This is a read-only closeout for the single already-claimed attempt.  It
    cannot upload, publish, retry, change prices or mutate campaign markers.
    """
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_attempt_id != PARTIAL_ATTEMPT_ID
            or expected_manifest_sha256 != PARTIAL_MANIFEST_SHA256):
        return _fail("partial_signup_audit_request_not_allowed")
    plan = db.get(CampaignPlan, PLAN_ID)
    if (plan is None or plan.workflow_key != WORKFLOW_KEY):
        return _fail("workflow_not_found")
    attempt = _load_attempt(db) or {}
    batches = attempt.get("batches") or []
    batch = batches[0] if len(batches) == 1 else {}
    validation = batch.get("terminal_validation") or {}
    actual_counts = {
        "total_items": validation.get("total_items"),
        "ok": validation.get("ok"),
        "failed": validation.get("failed"),
    }
    if (attempt.get("attempt_id") != PARTIAL_ATTEMPT_ID
            or attempt.get("manifest_sha256") != PARTIAL_MANIFEST_SHA256
            or attempt.get("status") not in (
                "failed_no_retry", "failed_unknown_outcome")
            or batch.get("status") not in (
                "failed_no_retry", "failed_unknown_outcome")
            or actual_counts != PARTIAL_TERMINAL_COUNTS):
        return _fail(
            "partial_signup_attempt_receipt_mismatch",
            actual_attempt_id=attempt.get("attempt_id"),
            actual_status=attempt.get("status"),
            actual_terminal_counts=actual_counts,
        )

    existing = db.execute(select(CampaignEvidenceSnapshot).where(
        CampaignEvidenceSnapshot.plan_id == PLAN_ID,
        CampaignEvidenceSnapshot.evidence_type == PARTIAL_AUDIT_EVIDENCE_TYPE,
    ).order_by(CampaignEvidenceSnapshot.id.desc())).scalars().first()
    if (existing is not None
            and (existing.platform_summary or {}).get("attempt_id")
            == PARTIAL_ATTEMPT_ID):
        summary = dict(existing.platform_summary or {})
        summary.update({
            "ok": True,
            "idempotent_replay": True,
            "snapshot_id": existing.id,
            "feedback_xlsx_b64": base64.b64encode(
                existing.failure_artifact_blob or b"").decode("ascii"),
            "execution_boundary": _execution_boundary(),
        })
        return summary

    manifest = attempt.get("manifest_rows") or []
    expected_rows = [{
        "taobao_item_id": str(row.get("item_id") or ""),
        "taobao_sku_id": str(row.get("sku_id") or ""),
        "sku_code": str(row.get("sku_code") or ""),
        "price": row.get("signup_price"),
        "is_placeholder": bool(row.get("is_custom_placeholder")),
    } for row in manifest]
    manifest_items = {
        str(row.get("taobao_item_id") or "") for row in expected_rows
    } - {""}
    if (manifest_items != AUTHORIZED_MISSING_ITEM_IDS
            or len(expected_rows) != EXPECTED_ROW_COUNT
            or _manifest_digest(expected_rows) != PARTIAL_MANIFEST_SHA256):
        return _fail("partial_signup_manifest_drift")

    from app.services import web_agent_service
    feedback_result = web_agent_service.super_reduce_feedback(db, timeout_s=600)
    if not feedback_result.get("ok"):
        return _fail(
            feedback_result.get("error") or "partial_signup_feedback_failed",
            step="official_failure_feedback",
            job_id=feedback_result.get("job_id"),
        )
    feedback_bytes = feedback_result.get("xlsx_bytes") or b""
    feedback = feedback_result.get("feedback") or {}
    failure_rows = feedback.get("failed") or []
    failed_item_ids = {
        str((row or {}).get("item_id") or "").strip()
        for row in failure_rows
        if str((row or {}).get("item_id") or "").strip()
    }
    bad_failure_pairs = [{
        "item_id": str((row or {}).get("item_id") or "").strip(),
        "sku_id": str((row or {}).get("sku_id") or "").strip(),
    } for row in failure_rows if (
        str((row or {}).get("item_id") or "").strip() not in manifest_items
        or str((row or {}).get("sku_id") or "").strip() not in {
            str(manifest_row.get("taobao_sku_id") or "")
            for manifest_row in expected_rows
            if str(manifest_row.get("taobao_item_id") or "")
            == str((row or {}).get("item_id") or "").strip()
        }
    )]
    if (len(feedback_bytes) < 100
            or len(failed_item_ids) != PARTIAL_TERMINAL_COUNTS["failed"]
            or not failed_item_ids <= manifest_items or bad_failure_pairs):
        return _fail(
            "partial_signup_feedback_scope_mismatch",
            failed_item_ids=sorted(failed_item_ids),
            bad_failure_pairs=bad_failure_pairs,
            feedback_sha256=hashlib.sha256(feedback_bytes).hexdigest(),
        )
    accepted_item_ids = manifest_items - failed_item_ids

    refreshed = campaign_service.refresh_floor_evidence_from_current_activity(
        db, plan, allow_placeholder_safe_lowering=True)
    if not refreshed.get("ok"):
        return _fail(
            refreshed.get("error") or "partial_signup_readback_failed",
            step=refreshed.get("step") or "official_enrolled_export",
            job_id=refreshed.get("job_id"),
            feedback_filename=feedback_result.get("filename"),
            feedback_sha256=hashlib.sha256(feedback_bytes).hexdigest(),
            feedback_xlsx_b64=base64.b64encode(feedback_bytes).decode("ascii"),
        )
    live_rows = refreshed.get("rows") or []
    verification: dict[str, dict] = {}
    official_exact: set[str] = set()
    official_active: set[str] = set()
    official_paused: set[str] = set()
    for item_id in sorted(manifest_items):
        item_rows = [
            row for row in expected_rows
            if str(row.get("taobao_item_id") or "") == item_id
        ]
        checked = _verify_all_skus(item_rows, live_rows)
        verification[item_id] = checked
        if not checked.get("ok"):
            continue
        official_exact.add(item_id)
        statuses = {
            status for row in checked.get("verified") or []
            for status in row.get("statuses") or []
        }
        if statuses and statuses <= {"活动中", "已发布设定"}:
            official_active.add(item_id)
        else:
            official_paused.add(item_id)

    discount_rows, _discount_stats = campaign_service.build_discount_rows(db, plan)
    exact_discount_rows = [
        row for row in discount_rows
        if str(row.get("taobao_item_id") or "") in manifest_items
    ]
    price_math = campaign_service._check_price_math(
        db, plan, expected_rows, exact_discount_rows)
    if price_math.get("level") != "pass":
        return _fail(
            "partial_signup_final_price_math_blocked",
            price_math=price_math,
            failed_item_ids=sorted(failed_item_ids),
        )

    feedback_sha = hashlib.sha256(feedback_bytes).hexdigest()
    summary = {
        "attempt_id": PARTIAL_ATTEMPT_ID,
        "manifest_sha256": PARTIAL_MANIFEST_SHA256,
        "terminal_counts": PARTIAL_TERMINAL_COUNTS,
        "platform_write_observed": True,
        "platform_write_kind": "partial_enrollment_import",
        "enrollment_record_created": True,
        "active": False,
        "published": None,
        "stopped_before": None,
        "accepted_item_ids": sorted(accepted_item_ids),
        "enrolled_paused_item_ids": sorted(official_paused),
        # Compatibility field retained as an explicit empty value so no caller
        # can interpret the enrolled paused records as publishable drafts.
        "draft_imported_item_ids": [],
        "failed_item_ids": sorted(failed_item_ids),
        "failure_groups": feedback.get("by_reason") or [],
        "failure_rows": failure_rows,
        "official_exact_item_ids": sorted(official_exact),
        "official_active_item_ids": sorted(official_active),
        "official_paused_or_pending_item_ids": sorted(official_paused),
        "official_not_exact_item_ids": sorted(manifest_items - official_exact),
        "per_item_sku_verification": verification,
        "final_price_math": price_math,
        "feedback_filename": feedback_result.get("filename"),
        "feedback_sha256": feedback_sha,
        "feedback_size": len(feedback_bytes),
        "feedback_job_id": feedback_result.get("job_id"),
        "enrolled_export": refreshed.get("export_evidence"),
        "safe_failed_only_recovery_available": False,
        "state_interpretation_version": 2,
        "recovery_blocker": (
            "two items already have enrolled paused records; they are not "
            "platform drafts and the global one-click publish action is unsafe"
        ),
    }
    snapshot = CampaignEvidenceSnapshot(
        plan_id=PLAN_ID,
        workflow_key=WORKFLOW_KEY,
        evidence_type=PARTIAL_AUDIT_EVIDENCE_TYPE,
        request_id=f"plan7-partial-audit-{PARTIAL_ATTEMPT_ID}",
        web_agent_job_id=str(feedback_result.get("job_id") or "") or None,
        scope_sha256=PARTIAL_MANIFEST_SHA256,
        result_status="partial_enrollment_audited",
        platform_summary=summary,
        rows=expected_rows,
        failure_rows=failure_rows,
        execution_boundary=_execution_boundary(),
        artifact_kind="campaign_enrolled_export",
        artifact_filename=(refreshed.get("export_evidence") or {}).get("filename"),
        artifact_sha256=(refreshed.get("export_evidence") or {}).get("sha256"),
        artifact_size=(refreshed.get("export_evidence") or {}).get("size"),
        failure_artifact_filename=feedback_result.get("filename"),
        failure_artifact_sha256=feedback_sha,
        failure_artifact_size=len(feedback_bytes),
        failure_artifact_blob=feedback_bytes,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return {
        "ok": True,
        **summary,
        "snapshot_id": snapshot.id,
        "feedback_xlsx_b64": base64.b64encode(feedback_bytes).decode("ascii"),
        "execution_boundary": _execution_boundary(),
    }


def _draft_publish_rows(summary: dict) -> list[dict]:
    verification = summary.get("per_item_sku_verification") or {}
    rows: list[dict] = []
    for item_id in sorted(DRAFT_PUBLISH_ITEM_IDS):
        checked = verification.get(item_id) or {}
        for row in checked.get("verified") or []:
            rows.append({
                "item_id": str(row.get("item_id") or ""),
                "sku_id": str(row.get("sku_id") or ""),
                "signup_price": round(
                    float(row.get("expected_activity_price")), 2),
                "is_placeholder": bool(row.get("is_custom_placeholder")),
            })
    return sorted(rows, key=lambda row: (row["item_id"], row["sku_id"]))


def _draft_publish_scope_sha256(rows: list[dict]) -> str:
    raw = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_draft_publish_receipt(db: Session) -> dict | None:
    raw = settings_service.get(
        db, DRAFT_PUBLISH_RECEIPT_KEY, env_fallback=False)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"status": "invalid"}
    return value if isinstance(value, dict) else {"status": "invalid"}


def _save_draft_publish_receipt(db: Session, payload: dict) -> None:
    settings_service.set_value(
        db,
        DRAFT_PUBLISH_RECEIPT_KEY,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        description=(
            "超级立减计划7两件既有草稿一次性原位发布回执（不含凭据）"),
    )


def _verify_draft_publish_live_rows(
        expected_rows: list[dict], live_rows: list[dict], *,
        required_statuses: set[str]) -> dict:
    live_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in live_rows:
        live_by_pair[(
            str(row.get("item_id") or ""),
            str(row.get("sku_id") or ""),
        )].append(row)
    failures = []
    verified = []
    for expected in expected_rows:
        pair = (expected["item_id"], expected["sku_id"])
        candidates = live_by_pair.get(pair) or []
        exact = [row for row in candidates if (
            row.get("activity_price") is not None
            and abs(float(row["activity_price"])
                    - float(expected["signup_price"])) <= 0.005
            and str(row.get("status") or "") in required_statuses
        )]
        if exact:
            verified.append({
                **expected,
                "statuses": sorted({
                    str(row.get("status") or "") for row in exact}),
            })
        else:
            failures.append({
                **expected,
                "actual_prices": [row.get("activity_price") for row in candidates],
                "actual_statuses": [str(row.get("status") or "")
                                    for row in candidates],
            })
    return {
        "ok": not failures,
        "checked_skus": len(expected_rows),
        "verified_skus": len(verified),
        "failures": failures,
    }


def publish_plan7_existing_drafts(
        db: Session, *, workflow_key: str, expected_plan_id: int,
        expected_attempt_id: str, expected_snapshot_id: int,
        expected_scope_sha256: str) -> dict:
    """Deprecated unsafe entry; enrolled paused records are not drafts."""
    if (workflow_key != WORKFLOW_KEY or expected_plan_id != PLAN_ID
            or expected_attempt_id != PARTIAL_ATTEMPT_ID
            or expected_scope_sha256 != DRAFT_PUBLISH_SCOPE_SHA256):
        return _fail("draft_publish_request_not_allowed")
    return _fail(
        "draft_publish_removed_paused_is_enrolled_state",
        reason=(
            "官方已报商品导出中的暂停记录已经是报名记录，不能按草稿调用全局一键发布；"
            "如需启用必须另走精确商品的原位启用能力并取得当前明确授权"
        ),
        execution_boundary=_execution_boundary(),
    )

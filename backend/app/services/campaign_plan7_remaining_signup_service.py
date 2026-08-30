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
AUTHORIZED_ITEM_SCOPE_SHA256 = (
    "d2ee3fd43a5c80d31799fc17b1b9f57c90db9f7ec7c78a0c88a501a7b51db2b8"
)
ATTEMPT_KEY = "campaign_plan7_remaining_signup_v1"
RECOVERY_INCIDENT_ID = "plan7-preclaim-export-e222849772c5"
MAX_ITEMS_PER_BATCH = 50
MAX_ROWS_PER_BATCH = 500
EXPECTED_ITEM_COUNT = 30
EXPECTED_ROW_COUNT = 299
EXPECTED_REAL_SKU_ROWS = 221
EXPECTED_PLACEHOLDER_SKU_ROWS = 78


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
            != AUTHORIZED_ITEM_SCOPE_SHA256:
        return _fail("remaining_signup_authorized_scope_constant_invalid")

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
    already_present = _active_or_pending_items(
        live_rows, AUTHORIZED_PLACEHOLDER_ITEM_IDS)
    if already_present:
        return _fail(
            "remaining_signup_scope_already_present_requires_review",
            item_ids=sorted(already_present),
            export_evidence=refreshed.get("export_evidence"),
        )

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
        in AUTHORIZED_PLACEHOLDER_ITEM_IDS
    ]
    actual_items = {str(row["taobao_item_id"]) for row in rows}
    if actual_items != AUTHORIZED_PLACEHOLDER_ITEM_IDS:
        return _fail(
            "remaining_signup_safe_scope_incomplete",
            missing_item_ids=sorted(AUTHORIZED_PLACEHOLDER_ITEM_IDS - actual_items),
            unexpected_item_ids=sorted(actual_items - AUTHORIZED_PLACEHOLDER_ITEM_IDS),
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
        exact_item_scope=AUTHORIZED_PLACEHOLDER_ITEM_IDS,
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
        "manifest_sha256": manifest_sha,
        "item_ids": sorted(actual_items),
        "item_count": len(actual_items),
        "row_count": len(rows),
        "real_sku_rows": price_sources["real_sku_rows"],
        "placeholder_sku_rows": price_sources["placeholder_sku_rows"],
        "manifest_rows": _manifest_rows(rows),
        "excluded": {
            "already_accepted": sorted(ACCEPTED_ITEM_IDS),
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

    completed_items = set(ACCEPTED_ITEM_IDS)
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

        attempt = _load_attempt(db) or attempt
        status = "completed" if batch_ok else (
            "failed_unknown_outcome" if submitted is None
            or (result or {}).get("unknown_outcome") else "failed_no_retry"
        )
        batch_receipt = {
            "status": status,
            "finished_at": _utcnow(),
            "submitted": submitted,
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
                "terminal_validation": (result or {}).get("validation"),
                "terminal_classification": classification,
                "post_submit_verification": verification,
                "automatic_retry": False,
            },
            export_evidence=export_evidence,
            failure_rows=(verification or {}).get("failures") or [],
            platform_write=bool(submitted),
        )
        db.commit()

        receipt_result = {
            "ok": batch_ok,
            "submitted": bool(submitted),
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
                    platform_write=bool(submitted)),
            }
        completed_items.update(batch["item_ids"])

    # All batches have exact terminals and exact per-SKU readback.  Preserve the
    # old accepted item and record a single full-scope terminal digest.
    plan = db.get(CampaignPlan, PLAN_ID)
    campaign_service._set_plan_item_marker(
        plan, "platform_qualified_items", completed_items)
    campaign_service._set_plan_item_marker(plan, "platform_no_sales_items", set())
    campaign_service._set_plan_item_marker(plan, "platform_hard_failed_items", set())
    all_safe_rows, _ = campaign_service.build_signup_rows(
        db, plan, allow_placeholder_safe_lowering=True)
    accepted_rows = [
        row for row in all_safe_rows
        if str(row.get("taobao_item_id") or "") in completed_items
    ]
    campaign_service._record_terminal_platform_acceptance(
        plan, accepted_rows, completed_items)
    plan.status = "signup_pushed"
    final_attempt = _load_attempt(db) or claimed
    final_attempt.update({
        "status": "completed",
        "completed_at": _utcnow(),
        "completed_item_ids": sorted(completed_items),
        "submitted_new_item_count": len(AUTHORIZED_PLACEHOLDER_ITEM_IDS),
        "submitted_new_row_count": len(rows),
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
        "item_count": len(AUTHORIZED_PLACEHOLDER_ITEM_IDS),
        "row_count": len(rows),
        "real_sku_rows": price_sources["real_sku_rows"],
        "placeholder_sku_rows": price_sources["placeholder_sku_rows"],
        "execution_boundary": _execution_boundary(platform_write=True),
    }

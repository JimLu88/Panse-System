from datetime import datetime

from sqlalchemy import select

from app import dependencies
from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_plan8_final_recovery_v2_service as recovery,
    campaign_policy_service,
    campaign_service,
    web_agent_service,
)


def _plan():
    return CampaignPlan(
        id=8,
        workflow_key=recovery.WORKFLOW_KEY,
        name="超级88现货",
        campaign_type="big88",
        tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="26年淘宝9月超级88",
        status="alarmed",
        remark="official_all_store=true; official_exempt_items=805268708396",
        platform_activity_mode="fixed_window",
        platform_campaign_id="49462",
        platform_united_activity_id="49469",
        platform_sign_record_id="3527841611",
    )


def _seed_prerequisites(db):
    for attempt_id, (operation, state, claimed) in (
            recovery.PREREQUISITE_ATTEMPTS.items()):
        db.add(CampaignExecutionAttempt(
            id=attempt_id,
            plan_id=8,
            workflow_key=recovery.WORKFLOW_KEY,
            operation=operation,
            scope_sha256=(attempt_id * 3)[:64],
            state=state,
            write_claimed=claimed,
            platform_write_observed=claimed,
            automatic_retry_allowed=False,
        ))
    db.commit()


def _signup_rows():
    counts = {
        "1036279566778": 20,
        "1036312802226": 5,
        "1074244132390": 20,
        "837902729785": 14,
        "841201084787": 11,
        "917179577721": 8,
    }
    rows = []
    sku = 6200000000000
    custom_left = 18
    for item_id, count in counts.items():
        for _ in range(count):
            sku += 1
            custom = custom_left > 0
            custom_left -= int(custom)
            rows.append({
                "taobao_item_id": item_id,
                "taobao_sku_id": str(sku),
                "sku_code": f"SKU-{sku}",
                "price": 1000.0,
                "is_placeholder": custom,
            })
    for _ in range(7):
        sku += 1
        rows.append({
            "taobao_item_id": "805268708396",
            "taobao_sku_id": str(sku),
            "sku_code": f"SKU-{sku}",
            "price": 1000.0,
            "is_placeholder": False,
        })
    return rows


def _discount_rows():
    rows = []
    for index, (item_id, sku_id) in enumerate(sorted(recovery.SUPPLEMENT_PAIRS)):
        rows.append({
            "taobao_item_id": item_id,
            "taobao_sku_id": sku_id,
            "sku_code": f"CODE-{index}",
            "deduct": 100.25 + index,
            "target_price": 500.0,
        })
    return rows


def _current(candidate_sha="c" * 64):
    marketing = []
    for item_id in sorted(recovery.EXPECTED_OFFICIAL_RECORD_ITEM_IDS):
        selected = item_id in recovery.EXPECTED_ALREADY_PUBLISHED_ITEM_IDS
        marketing.append({
            "item_id": item_id,
            "selected": selected,
            "proves_enrollment": selected,
        })
    return {
        "ok": True,
        "rows": [{"item_id": item_id, "sku_id": f"sku-{item_id}"}
                 for item_id in sorted(
                     recovery.EXPECTED_ALREADY_PUBLISHED_ITEM_IDS)],
        "floor_refresh": {"recorded": 85},
        "candidate_evidence": {
            "sha256": candidate_sha,
            "requested_sku_count": 179,
            "observed_sku_count": 179,
            "missing_sku_ids": [],
            "candidate_items_scanned": 50,
            "page_count": 6,
        },
        "candidate_unavailable": {
            "complete": True,
            "items": ["793202812082"],
            "partial_missing_items": [],
            "sha256": candidate_sha,
        },
        "export_evidence": {
            "sha256": "d" * 64,
            "identity": {
                "campaign_title": "26年淘宝9月超级88",
                "campaign_id": "49462",
                "united_activity_id": "49469",
                "sign_record_id": "3527841611",
                "campaign_start": "2026-09-06 20:00:00",
                "campaign_end": "2026-09-13 23:59:59",
                "official_rate": "12%",
                "platform_activity_mode": "fixed_window",
            },
            "marketing_records": marketing,
        },
    }


def test_plan8_v2_has_new_route_and_exact_fixed_scope():
    assert dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V2_PATH == (
        "/api/campaigns/recover-super88-plan8-final-v2")
    assert dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V2_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert len(recovery.SUPPLEMENT_PAIRS) == 8
    assert len(recovery.EXPECTED_TARGET_ITEM_IDS) == 6
    assert recovery.EXPECTED_TARGET_ROW_COUNT == 78
    assert recovery.EXPECTED_TARGET_CUSTOM_ROW_COUNT == 18


def test_plan8_v2_requires_fresh_complete_candidate_evidence():
    ok, detail = recovery.validate_prepared_current_activity(_current())
    assert ok is True
    assert detail["candidate_requested_skus"] == 179
    stale, _ = recovery.validate_prepared_current_activity(
        _current(recovery.OLD_CANDIDATE_SHA256))
    assert stale is False
    incomplete = _current()
    incomplete["candidate_evidence"]["observed_sku_count"] = 171
    incomplete["candidate_evidence"]["missing_sku_ids"] = ["1"] * 8
    incomplete_ok, _ = recovery.validate_prepared_current_activity(incomplete)
    assert incomplete_ok is False


def test_plan8_v2_supplements_only_eight_then_enrolls_78(
        db_session, monkeypatch):
    monkeypatch.setattr(recovery, "V2_RETIRED", False)
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    signup_rows = _signup_rows()
    discount_rows = _discount_rows()
    current = _current()
    monkeypatch.setattr(
        campaign_policy_service, "require_policy",
        lambda: {"version": "test", "_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        campaign_service, "build_signup_rows",
        lambda *_a, **_k: (list(signup_rows), {"rows": len(signup_rows)}))
    monkeypatch.setattr(
        campaign_service, "build_discount_rows",
        lambda *_a, **_k: (list(discount_rows), {"rows": len(discount_rows)}))
    monkeypatch.setattr(
        recovery, "_scope_sha",
        lambda _identity, rows, _policy: (
            recovery.EXPECTED_FULL_SCOPE_SHA256
            if len(rows) == 85 else recovery.EXPECTED_TARGET_SCOPE_SHA256))
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_a, **_k: current)
    monkeypatch.setattr(
        campaign_service, "candidate_unavailable_items_for_plan",
        lambda *_a, **_k: {
            "793202812082": {"evidence_sha256": "c" * 64},
        })
    monkeypatch.setattr(
        campaign_service, "preflight", lambda *_a, **_k: [
            {"rule": "R16", "level": "pass"},
            {"rule": "R17", "level": "pass"},
        ])
    monkeypatch.setattr(
        campaign_service, "_refresh_official_product_sku_identity",
        lambda *_a, **_k: {
            "ok": True, "checked_items": 6, "checked_skus": 78,
            "official_skus": 78, "excluded_custom_skus": 0,
        })
    inspect_scope = recovery._discount_scope(discount_rows)
    inspect_sha = recovery._discount_scope_sha(inspect_scope)
    inspect_rows = [{**row, "actual_deduct": None, "state": "missing",
                     "activity_id": "143900000001"}
                    for row in inspect_scope]
    monkeypatch.setattr(
        web_agent_service, "inspect_plan8_final_discount_supplement",
        lambda *_a, **_k: {
            "ok": True,
            "scope_sha256": inspect_sha,
            "items": [
                {"item_id": "1036279566778", "activity_id": "143900000001"},
                {"item_id": "1074244132390", "activity_id": "143900000001"},
            ],
            "rows": inspect_rows,
            "missing_skus": sorted(recovery.SUPPLEMENT_SKU_IDS),
            "correct_skus": [],
            "wrong_skus": [],
        })
    uploads = []

    def fake_upload(*_args, **kwargs):
        uploads.append(kwargs)
        return {"ok": True, "submitted": True, "job": f"job-{len(uploads)}"}

    monkeypatch.setattr(campaign_service, "_upload_and_wait", fake_upload)

    def fake_signup(db, plan, **kwargs):
        assert kwargs["exact_item_scope"] == recovery.EXPECTED_TARGET_ITEM_IDS
        assert kwargs["execution_source"] == (
            "campaign_super88_plan8_final_recovery_v2")
        assert len(signup_rows) == 85
        plan.status = "signup_pushed"
        db.commit()
        return {
            "ok": True,
            "submitted": True,
            "job": "signup-job",
            "stats": {"execution_attempt_id": "inner-signup-attempt"},
            "post_submit_verification": {"ok": True},
        }

    monkeypatch.setattr(campaign_service, "push_signup", fake_signup)

    result = recovery.recover_plan8_final_v2(
        db_session,
        workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8,
        expected_status="alarmed",
        recovery_version=2,
    )

    assert result["ok"] is True
    assert result["discount_rows_written"] == 8
    assert len(uploads) == 2
    assert all(kwargs["expected_rows"] == 4 for kwargs in uploads)
    assert result["signup_attempt_id"] == "inner-signup-attempt"
    attempt = db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one()
    assert attempt.state == "completed"
    assert attempt.write_claimed is True
    assert attempt.platform_write_observed is True


def test_push_signup_v2_source_rejects_unprepared_context(
        db_session, monkeypatch):
    plan = _plan()
    plan.status = "resume_executing"
    db_session.add(plan)
    db_session.commit()
    monkeypatch.setattr(
        campaign_policy_service, "require_policy",
        lambda: {"version": "test", "_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("V2 must reuse its prepared current export")))

    result = campaign_service.push_signup(
        db_session,
        plan,
        execution_source="campaign_super88_plan8_final_recovery_v2",
        reuse_fresh_plan_evidence=True,
        exact_item_scope=recovery.EXPECTED_TARGET_ITEM_IDS,
        prepared_current_activity={},
        prepared_official_product_identity={"ok": False},
    )

    assert result["ok"] is False
    assert result["step"] == "plan8_final_v2_policy_guard"
    assert plan.status == "resume_executing"

"""One-shot Super Reduce plan-7 resume: scope, evidence, CAS and no retry."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.campaign import CampaignPlan
from app.services import (
    campaign_price_floor_service,
    campaign_resume_service,
    campaign_service,
)


def _plan() -> CampaignPlan:
    return CampaignPlan(
        id=7,
        workflow_key=campaign_resume_service.WORKFLOW_KEY,
        name="2026-09-01超级立减更新窗口",
        campaign_type="super_reduce",
        tier="mid",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 1, 23, 59, 59),
        qn_campaign_title="超级立减",
        status="alarmed",
        remark=(
            "platform_qualified_items=; platform_no_sales_items=; "
            "platform_hard_failed_items=; current_activity_prices=; "
            "official_all_store=true; official_exempt_items=805268708396"
        ),
        platform_activity_mode="long_running_update",
        platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
    )


def _signup_rows() -> list[dict]:
    return [
        {"taobao_item_id": "797294092429", "taobao_sku_id": "6292834839399",
         "sku_code": "PPS2438001051311", "price": 1582.5,
         "is_placeholder": False, "remark": None},
        {"taobao_item_id": "797294092429", "taobao_sku_id": "6292834839400",
         "sku_code": "PPS2438001051312", "price": 1410.0,
         "is_placeholder": False, "remark": None},
    ]


def _discount_rows() -> list[dict]:
    return [
        {"taobao_item_id": "797294092429", "taobao_sku_id": "6292834839399",
         "sku_code": "PPS2438001051311", "deduct": 488.1,
         "target_price": 935.4, "official": 159.0,
         "calculation_base": 1582.5},
        {"taobao_item_id": "797294092429", "taobao_sku_id": "6292834839400",
         "sku_code": "PPS2438001051312", "deduct": 438.7,
         "target_price": 830.3, "official": 141.0,
         "calculation_base": 1410.0},
    ]


def _request() -> dict:
    return {
        "workflow_key": campaign_resume_service.WORKFLOW_KEY,
        "expected_plan_id": 7,
        "expected_status": "alarmed",
        "expected_scope_sha256": campaign_resume_service.EXPECTED_SCOPE_SHA256,
    }


def _seed_fresh_evidence(db, plan) -> None:
    campaign_price_floor_service.record_activity_export(
        db,
        [
            {"item_id": "797294092429", "sku_id": "6292834839399",
             "min_list_price": 2110.0, "min_coupon_line": 2110.0},
            {"item_id": "797294092429", "sku_id": "6292834839400",
             "min_list_price": 1880.0, "min_coupon_line": 1880.0},
        ],
        source="campaign_pre_submit_export:plan=7",
        observed_at=datetime.now(timezone.utc),
        plan=plan,
    )
    db.commit()


def _patch_safe_package(monkeypatch):
    monkeypatch.setattr(
        campaign_service, "build_signup_rows",
        lambda *_args, **_kwargs: (_signup_rows(), {}))
    monkeypatch.setattr(
        campaign_service, "build_discount_rows",
        lambda *_args, **_kwargs: (_discount_rows(), {}))
    monkeypatch.setattr(
        campaign_service, "preflight",
        lambda *_args, **kwargs: [
            {"rule": "R16", "level": "pass"},
            {"rule": "R17", "level": "pass", "checked": 2},
        ] if kwargs.get("exact_item_scope") == {"797294092429"} else [
            {"rule": "R17", "level": "error", "checked": 0},
        ])


def test_plan7_resume_claims_once_and_replays_without_second_write(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    _seed_fresh_evidence(db_session, plan)
    _patch_safe_package(monkeypatch)
    calls = []

    def fake_push(db, current_plan, **kwargs):
        calls.append(kwargs)
        assert current_plan.status == "resume_executing"
        current_plan.status = "signup_pushed"
        db.commit()
        return {"ok": True, "submitted": True, "job": "job-plan7"}

    monkeypatch.setattr(campaign_service, "push_signup", fake_push)
    first = campaign_resume_service.resume_super_reduce_plan7(
        db_session, **_request())
    replay = campaign_resume_service.resume_super_reduce_plan7(
        db_session, **_request())

    assert first["ok"] is True and first["attempt"]["status"] == "completed"
    assert first["scope_sha256"] == campaign_resume_service.EXPECTED_SCOPE_SHA256
    assert first["execution_boundary"]["platform_write"] is True
    assert first["execution_boundary"]["pre_submit_platform_read"] is False
    assert replay["ok"] is True and replay["idempotent_replay"] is True
    assert len(calls) == 1
    assert calls[0] == {
        "execution_source": "campaign_super_reduce_plan7_resume",
        "reuse_fresh_plan_evidence": True,
        "exact_item_scope": {"797294092429"},
        "allow_terminal_no_sales_fallback": False,
    }


def test_plan7_resume_rejects_plan8_scope_drift_and_stale_evidence(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    _seed_fresh_evidence(db_session, plan)
    _patch_safe_package(monkeypatch)
    monkeypatch.setattr(campaign_service, "push_signup", lambda *_a, **_k: None)

    wrong_plan = campaign_resume_service.resume_super_reduce_plan7(
        db_session, **{**_request(), "expected_plan_id": 8})
    assert wrong_plan["error"] == "resume_request_not_allowed"

    rows = _signup_rows()
    rows[0]["price"] = 1582.49
    monkeypatch.setattr(
        campaign_service, "build_signup_rows",
        lambda *_args, **_kwargs: (rows, {}))
    drift = campaign_resume_service.resume_super_reduce_plan7(
        db_session, **_request())
    assert drift["error"] == "resume_scope_drift"
    assert plan.status == "alarmed"


def test_resume_push_skips_pre_submit_refresh_and_disables_no_sales_fallback(
        db_session, monkeypatch):
    plan = _plan()
    plan.status = "resume_executing"
    db_session.add(plan)
    db_session.commit()
    refresh_calls = []
    discount_calls = []

    monkeypatch.setattr(
        campaign_service, "preflight", lambda *_a, **_k: [])
    monkeypatch.setattr(
        campaign_service, "build_signup_rows",
        lambda *_a, **_k: (_signup_rows(), {}))
    monkeypatch.setattr(
        campaign_service, "_check_placeholder_live_prices",
        lambda _stats: {"level": "pass"})
    monkeypatch.setattr(
        campaign_service, "_build_super_signup_xlsx", lambda _rows: b"xlsx")
    monkeypatch.setattr(
        campaign_service, "_upload_and_wait",
        lambda *_a, **_k: {
            "ok": True, "submitted": True, "job": "job1",
            "validation": {"total_items": 1, "ok": 1, "failed": 0},
        })
    monkeypatch.setattr(
        campaign_service, "_learn_from_validation", lambda *_a, **_k: {})
    monkeypatch.setattr(
        campaign_service, "_classify_final_signup",
        lambda *_a, **_k: {
            "ok": True, "accepted_item_ids": [],
            "no_sales_item_ids": ["797294092429"],
            "hard_failed_item_ids": [],
        })
    monkeypatch.setattr(
        campaign_service, "push_discount",
        lambda *_a, **_k: discount_calls.append(True))
    monkeypatch.setattr(
        campaign_service, "_notify_signup_failure", lambda *_a, **_k: {})

    def fake_refresh(*_args, **_kwargs):
        refresh_calls.append(True)
        return {"ok": True, "rows": [], "floor_refresh": {}}

    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        fake_refresh)
    result = campaign_service.push_signup(
        db_session,
        plan,
        execution_source="campaign_super_reduce_plan7_resume",
        reuse_fresh_plan_evidence=True,
        exact_item_scope={"797294092429"},
        allow_terminal_no_sales_fallback=False,
    )

    assert result["ok"] is False
    assert result["step"] == "terminal_no_sales_requires_new_decision"
    assert refresh_calls == []
    assert discount_calls == []
    assert plan.status == "alarmed"


def test_resume_push_internal_guard_rejects_plan8(db_session, monkeypatch):
    plan = _plan()
    plan.id = 8
    plan.workflow_key = "campaign:super88:49462:49469"
    plan.campaign_type = "big88"
    plan.status = "resume_executing"
    db_session.add(plan)
    db_session.commit()
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("plan8 must not read or write platform state")))

    result = campaign_service.push_signup(
        db_session,
        plan,
        execution_source="campaign_super_reduce_plan7_resume",
        reuse_fresh_plan_evidence=True,
        exact_item_scope={"797294092429"},
        allow_terminal_no_sales_fallback=False,
    )

    assert result["ok"] is False
    assert result["step"] == "resume_execution_policy_guard"
    assert plan.status == "resume_executing"


def test_exact_preflight_scope_drops_unrelated_r16_diagnostics(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    unrelated = {
        "taobao_item_id": "OTHER_ITEM",
        "taobao_sku_id": "OTHER_SKU",
        "current_live_price": None,
    }
    stats = {
        "incomplete_items": [unrelated],
        "placeholder_missing_live_price": [unrelated],
        "placeholder_price_blocked_items": [unrelated],
        "placeholder_price_lowered": [unrelated],
        "excluded_no_sales_items": [],
        "line_concessions": [],
        "skipped_delisted": 0,
        "official_ceil": False,
    }
    monkeypatch.setattr(
        campaign_service, "build_signup_rows",
        lambda *_a, **_k: (_signup_rows(), dict(stats)))
    monkeypatch.setattr(
        campaign_service, "build_discount_rows",
        lambda *_a, **_k: (_discount_rows(), {
            "line_concessions": [], "skipped_delisted": 0,
            "official_ceil": False,
        }))
    monkeypatch.setattr(
        campaign_service, "_check_campaign_policy",
        lambda: {"rule": "R0", "level": "pass", "items": []})
    monkeypatch.setattr(
        campaign_service, "_check_price_floor_evidence",
        lambda *_a, **_k: {"rule": "R17", "level": "pass", "checked": 2})
    monkeypatch.setattr(
        campaign_service, "price_hold_items", lambda *_a, **_k: [])

    checks = campaign_service.preflight(
        db_session, plan, exact_item_scope={"797294092429"})
    by_rule = {row["rule"]: row for row in checks}

    assert by_rule["R3"]["level"] == "pass"
    assert by_rule["R16"]["level"] == "pass"
    assert by_rule["R16"]["items"] == []
    assert by_rule["R16"]["blocked_items"] == []

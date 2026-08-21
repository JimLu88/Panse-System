"""Permanent regression gates for the 2026-08-05 campaign execution contract."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.models.campaign import CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_notification_service
from app.services import campaign_policy_service, campaign_price_floor_service
from app.services import campaign_service


def _plan(db, campaign_type="big88", tier="big"):
    plan = CampaignPlan(
        name="程序边界测试",
        campaign_type=campaign_type,
        tier=tier,
        start_at=datetime(2026, 8, 10, 0, 0, 0),
        end_at=datetime(2026, 8, 12, 23, 59, 59),
        status="draft",
    )
    db.add(plan)
    db.commit()
    return plan


def _sku(db, *, daily=3000, list_price=4000, big=2000,
         legacy_list=100, legacy_coupon=100, sku_name="1.8米",
         product_name="报名规则测试商品"):
    db.add(PricingSku(
        product_code="PPS_POLICY",
        sku_code="PPS_POLICY_1",
        sku=sku_name,
        product_name=product_name,
        list_price=Decimal(str(list_price)),
        daily_price=Decimal(str(daily)),
    ))
    db.add(PricingSkuPromo(
        sku_code="PPS_POLICY_1",
        taobao_item_id="991880805",
        taobao_sku_id="881880805",
        big_buyer_price=Decimal(str(big)),
        enrolled_floor_price=Decimal(str(legacy_list)),
        coupon_floor_price=Decimal(str(legacy_coupon)),
    ))
    db.commit()


def test_root_policy_locks_program_and_real_sku_daily_price():
    policy = campaign_policy_service.require_policy()
    assert policy["execution"]["signup_executor"] == "campaign_automation_program_only"
    assert policy["execution"]["ai_may_submit"] is False
    assert policy["execution"]["ai_may_adjust_price"] is False
    assert policy["execution"]["automatic_campaign_withdrawal_enabled"] is False
    assert policy["execution"]["withdrawal_requires_current_explicit_item_list_authorization"] is True
    assert policy["pricing"]["real_sku_signup_price"] == "erp_daily_price"
    assert policy["qualification_gates"]["single_item_discount_participates_in_qualification"] is True
    assert policy["final_price_gate"]["explicit_sub_yuan_concession_max_yuan_exclusive"] == 1.00
    scope = policy["scope_and_idempotency"]
    assert scope["exclude_no_sales_items_from_campaign_signup"] is False
    assert scope["registered_no_sales_is_advisory_only"] is True
    assert scope["every_listed_item_is_requalified_by_platform_for_each_campaign"] is True
    assert scope["qualification_before_discount_and_final_signup"] is True
    assert scope["accepted_item_action"] == "single_item_discount_first_then_final_campaign_signup"
    assert scope["existing_single_discount_edit_mode"] == "one_item_per_job_with_sku_readback"
    assert scope["existing_single_discount_activity_binding"] == "per_item_id_to_activity_id"
    assert scope["qualification_hard_failure_action"] == "isolate_whole_item_report_and_continue_safe_items"
    assert "withdrawal_requires_current_explicit_item_list_authorization" in policy[
        "post_submit"]["active_activity_records_outside_current_scope"]


def test_production_image_bundles_the_root_policy():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "backend" / "Dockerfile").read_text(encoding="utf-8")
    deploy_script = (root / "scripts" / "deploy_api_nas.sh").read_text(encoding="utf-8")
    assert "COPY TAOBAO_CAMPAIGN_SIGNUP_POLICY.json /app/TAOBAO_CAMPAIGN_SIGNUP_POLICY.json" in dockerfile
    assert 'docker build . -f backend/Dockerfile' in deploy_script
    assert 'img_policy_sha=' in deploy_script
    assert deploy_script.count('MSYS_NO_PATHCONV=1 docker run') >= 2
    assert deploy_script.count('TAOBAO_CAMPAIGN_SIGNUP_POLICY.json') >= 4


def test_legacy_floor_fields_cannot_lower_real_signup_or_final_price(db_session):
    plan = _plan(db_session)
    _sku(db_session)

    signup, _ = campaign_service.build_signup_rows(db_session, plan)
    discounts, stats = campaign_service.build_discount_rows(db_session, plan)

    assert signup[0]["price"] == 3000.0
    assert discounts[0]["target_price"] == 2000.0
    assert discounts[0]["deduct"] == 640.0
    assert stats["line_concessions"] == []


def test_current_user_can_authorize_named_sub_yuan_discount_concession(db_session):
    plan = _plan(db_session)
    plan.remark = "line_concession_authorized=881880805:0.27"
    _sku(db_session)

    signup, _ = campaign_service.build_signup_rows(db_session, plan)
    discounts, stats = campaign_service.build_discount_rows(db_session, plan)

    assert signup[0]["price"] == 3000.0
    assert discounts[0]["official"] == 360.0
    assert discounts[0]["deduct"] == 640.27
    assert discounts[0]["target_price"] == 1999.73
    assert discounts[0]["concession"] == 0.27
    assert stats["line_concessions"] == [{
        "taobao_item_id": "991880805",
        "taobao_sku_id": "881880805",
        "sku_code": "PPS_POLICY_1",
        "amount": 0.27,
        "erp_target": 2000.0,
        "authorized_target": 1999.73,
        "authorization": "named_sub_yuan",
    }]


def test_line_concession_rejects_one_yuan_or_more(db_session):
    plan = _plan(db_session)
    plan.remark = "line_concession_authorized=881880805:1.00"
    _sku(db_session)

    discounts, stats = campaign_service.build_discount_rows(db_session, plan)

    assert discounts[0]["deduct"] == 640.0
    assert discounts[0]["target_price"] == 2000.0
    assert stats["line_concessions"] == []


def test_named_custom_sku_concession_can_exceed_one_yuan(db_session):
    plan = _plan(db_session)
    plan.remark = "custom_line_concession_authorized=881880805:33.00"
    _sku(db_session, sku_name="樱桃木定制（咨询客服）")

    signup, _ = campaign_service.build_signup_rows(db_session, plan)
    discounts, stats = campaign_service.build_discount_rows(db_session, plan)

    assert signup[0]["price"] == 3000.0
    assert discounts[0]["deduct"] == 673.0
    assert discounts[0]["target_price"] == 1967.0
    assert discounts[0]["concession"] == 33.0
    assert stats["line_concessions"] == [{
        "taobao_item_id": "991880805",
        "taobao_sku_id": "881880805",
        "sku_code": "PPS_POLICY_1",
        "amount": 33.0,
        "erp_target": 2000.0,
        "authorized_target": 1967.0,
        "authorization": "named_custom_price_sku",
    }]


def test_custom_sku_concession_marker_is_ignored_for_normal_sku(db_session):
    plan = _plan(db_session)
    plan.remark = "custom_line_concession_authorized=881880805:33.00"
    _sku(db_session)

    discounts, stats = campaign_service.build_discount_rows(db_session, plan)

    assert discounts[0]["deduct"] == 640.0
    assert discounts[0]["target_price"] == 2000.0
    assert stats["line_concessions"] == []


def test_named_custom_sku_concession_clears_matching_coupon_floor_hold(db_session):
    plan = _plan(db_session)
    plan.remark = "custom_line_concession_authorized=881880805:33.00"
    _sku(db_session, sku_name="定制")
    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805",
            "sku_id": "881880805",
            "sku_name": "定制",
            "min_list_price": 3000,
            "min_coupon_line": 1967.0,
        }],
        source="pytest_authorized_custom_floor",
    )
    db_session.commit()

    holds = campaign_service.price_hold_items(db_session, plan)
    discounts, _ = campaign_service.build_discount_rows(db_session, plan)

    assert holds == []
    assert discounts[0]["deduct"] == 673.0
    assert discounts[0]["target_price"] == 1967.0


def test_missing_platform_floor_evidence_blocks_before_upload(db_session):
    plan = _plan(db_session)
    _sku(db_session, legacy_list=4000, legacy_coupon=3000)

    checks = {row["rule"]: row for row in campaign_service.preflight(db_session, plan)}
    assert checks["R17"]["level"] == "error"
    assert checks["R17"]["items"][0]["missing"] == [
        "min_list_price", "min_coupon_line"]


def test_fresh_terminal_platform_acceptance_is_exact_r17_evidence(db_session):
    plan = _plan(db_session)
    _sku(db_session, legacy_list=4000, legacy_coupon=3000)
    signup_rows, _ = campaign_service.build_signup_rows(db_session, plan)
    campaign_service._record_terminal_platform_acceptance(
        plan, signup_rows, {"991880805"})
    db_session.commit()

    checks = {row["rule"]: row for row in campaign_service.preflight(db_session, plan)}

    assert checks["R17"]["level"] == "pass"
    assert checks["R17"]["platform_terminal_acceptance"]["accepted_item_count"] == 1
    assert checks["R17"]["platform_terminal_accepted_rows"][0][
        "taobao_sku_id"] == "881880805"


def test_stale_terminal_platform_acceptance_does_not_bypass_r17(db_session):
    plan = _plan(db_session)
    _sku(db_session, legacy_list=4000, legacy_coupon=3000)
    signup_rows, _ = campaign_service.build_signup_rows(db_session, plan)
    campaign_service._record_terminal_platform_acceptance(
        plan, signup_rows, {"991880805"})
    campaign_service._set_plan_value_marker(
        plan,
        "platform_terminal_accepted_observed_at",
        (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
    )
    db_session.commit()

    checks = {row["rule"]: row for row in campaign_service.preflight(db_session, plan)}

    assert checks["R17"]["level"] == "error"
    assert checks["R17"]["platform_terminal_acceptance"]["accepted_item_count"] == 0


def test_provisional_qualified_marker_alone_does_not_bypass_r17(db_session):
    plan = _plan(db_session)
    _sku(db_session, legacy_list=4000, legacy_coupon=3000)
    campaign_service._set_plan_item_marker(
        plan, "platform_qualified_items", {"991880805"})
    db_session.commit()

    checks = {row["rule"]: row for row in campaign_service.preflight(db_session, plan)}

    assert checks["R17"]["level"] == "error"
    assert checks["R17"]["platform_terminal_acceptance"]["accepted_item_count"] == 0


def test_blank_coupon_gate_in_fresh_export_is_observed_not_missing(db_session):
    plan = _plan(db_session)
    _sku(db_session, legacy_list=4000, legacy_coupon=3000)
    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805", "sku_id": "881880805", "sku_name": "1.8米",
            "min_list_price": 3200, "min_coupon_line": None,
        }],
        source="pytest_blank_coupon_gate",
    )
    db_session.commit()

    checks = {row["rule"]: row for row in campaign_service.preflight(db_session, plan)}
    assert checks["R17"]["level"] == "pass"


def test_duplicate_export_rows_keep_strictest_numeric_floor(db_session):
    campaign_price_floor_service.record_activity_export(
        db_session,
        [
            {"item_id": "991880805", "sku_id": "881880805",
             "min_list_price": 3200, "min_coupon_line": None},
            {"item_id": "991880805", "sku_id": "881880805",
             "min_list_price": 3000, "min_coupon_line": 2100},
            {"item_id": "991880805", "sku_id": "881880805",
             "min_list_price": 3400, "min_coupon_line": None},
        ],
        source="pytest_duplicate_marketing_records",
    )
    db_session.commit()

    entry = campaign_price_floor_service.evidence_map(db_session)["881880805"]
    assert entry["min_list_price"] == 3000.0
    assert entry["min_coupon_line"] == 2100.0
    assert entry["min_coupon_line_observed"] is True


def test_campaign_floor_evidence_isolated_by_plan(db_session):
    super_plan = _plan(db_session, campaign_type="super_reduce", tier="mid")
    school_plan = _plan(db_session, campaign_type="big88", tier="big")

    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805", "sku_id": "881880805",
            "min_list_price": 3000, "min_coupon_line": 1999.73,
        }],
        source="pytest_super_export",
        plan=super_plan,
    )
    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805", "sku_id": "881880805",
            "min_list_price": 3000, "min_coupon_line": 1888.88,
        }],
        source="pytest_school_export",
        plan=school_plan,
    )
    db_session.commit()

    super_entry = campaign_price_floor_service.evidence_map(
        db_session, plan=super_plan)["881880805"]
    school_entry = campaign_price_floor_service.evidence_map(
        db_session, plan=school_plan)["881880805"]

    assert super_entry["min_coupon_line"] == 1999.73
    assert super_entry["source"] == "pytest_super_export"
    assert school_entry["min_coupon_line"] == 1888.88
    assert school_entry["source"] == "pytest_school_export"


def test_plan_scoped_floor_refresh_drives_only_that_plan_preflight(db_session):
    blocked_plan = _plan(db_session)
    ready_plan = _plan(db_session)
    _sku(db_session, legacy_list=9999, legacy_coupon=9999)

    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805", "sku_id": "881880805",
            "min_list_price": 3000, "min_coupon_line": 1999.73,
        }],
        source="pytest_blocked_plan",
        plan=blocked_plan,
    )
    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805", "sku_id": "881880805",
            "min_list_price": 3000, "min_coupon_line": 2000,
        }],
        source="pytest_ready_plan",
        plan=ready_plan,
    )
    db_session.commit()

    assert len(campaign_service.price_hold_items(db_session, blocked_plan)) == 1
    assert campaign_service.price_hold_items(db_session, ready_plan) == []


def test_explicit_new_item_without_history_is_narrowly_allowed(db_session):
    plan = _plan(db_session)
    plan.remark = "new_item_no_history_authorized=991880805"
    _sku(db_session, legacy_list=4000, legacy_coupon=3000)

    checks = {row["rule"]: row for row in campaign_service.preflight(db_session, plan)}

    assert checks["R17"]["level"] == "pass"
    assert checks["R17"]["authorized_new_item_rows"][0]["taobao_item_id"] == "991880805"


def test_explicit_new_item_without_history_still_requires_current_list_ceiling(db_session):
    plan = _plan(db_session)
    plan.remark = "new_item_no_history_authorized=991880805"
    _sku(db_session, daily=3000, list_price=2999,
         legacy_list=4000, legacy_coupon=3000)

    checks = {row["rule"]: row for row in campaign_service.preflight(db_session, plan)}

    assert checks["R17"]["level"] == "error"
    assert checks["R17"]["items"][0]["missing"] == [
        "current_erp_list_price_ceiling"]


def test_fresh_platform_lines_include_single_item_discount_and_hold_whole_item(db_session):
    plan = _plan(db_session)
    _sku(db_session, legacy_list=9999, legacy_coupon=9999)
    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805",
            "sku_id": "881880805",
            "sku_name": "1.8米",
            "min_list_price": 3000,
            "min_coupon_line": 1999.73,
        }],
        source="pytest_fresh_export",
    )
    db_session.commit()

    holds = campaign_service.price_hold_items(db_session, plan)
    signup, _ = campaign_service.build_signup_rows(db_session, plan)
    discounts, _ = campaign_service.build_discount_rows(db_session, plan)

    assert [row["taobao_item_id"] for row in holds] == ["991880805"]
    reason = holds[0]["skus"][0]["reasons"][0]
    assert reason["type"] == "coupon_floor"
    assert reason["platform_coupon_after"] == 2000.0
    assert reason["difference"] == 0.27
    assert reason["single_item_discount_included_by_platform"] is True
    assert signup == []
    assert discounts == []


def test_named_sub_yuan_concession_clears_matching_coupon_floor_hold(db_session):
    plan = _plan(db_session)
    plan.remark = "line_concession_authorized=881880805:0.27"
    _sku(db_session, legacy_list=9999, legacy_coupon=9999)
    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805",
            "sku_id": "881880805",
            "sku_name": "1.8米",
            "min_list_price": 3000,
            "min_coupon_line": 1999.73,
        }],
        source="pytest_authorized_sub_yuan_floor",
    )
    db_session.commit()

    holds = campaign_service.price_hold_items(db_session, plan)
    signup, signup_stats = campaign_service.build_signup_rows(db_session, plan)
    discounts, discount_stats = campaign_service.build_discount_rows(db_session, plan)

    assert holds == []
    assert signup[0]["price"] == 3000.0
    assert signup_stats["excluded_price_hold_items"] == []
    assert discounts[0]["deduct"] == 640.27
    assert discounts[0]["target_price"] == 1999.73
    assert discount_stats["excluded_price_hold_items"] == []


def test_ai_or_page_direct_signup_is_rejected_without_platform_call(db_session, monkeypatch):
    plan = _plan(db_session)
    called = []
    from app.services import web_agent_service
    monkeypatch.setattr(
        web_agent_service,
        "campaign_export_items",
        lambda *args, **kwargs: called.append((args, kwargs)) or {"ok": True},
    )

    result = campaign_service.push_signup(db_session, plan, execution_source="ai")

    assert result["ok"] is False
    assert result["step"] == "execution_policy_guard"
    assert result["ai_may_adjust_or_resubmit"] is False
    assert called == []


def test_campaign_notifications_can_be_disabled_without_affecting_other_alerts(
        db_session, monkeypatch):
    from app.services import notify_service, settings_service

    delivered = []
    monkeypatch.setattr(
        notify_service,
        "broadcast_text",
        lambda *args, **kwargs: delivered.append((args, kwargs)) or {"feishu": True},
    )
    settings_service.set_value(
        db_session, campaign_notification_service.SETTING_KEY, "false")
    db_session.commit()

    result = campaign_notification_service.broadcast_text(
        db_session, "只在当前对话汇报", title="活动自动执行失败", level="error")

    assert result["skipped"] == "campaign_notifications_disabled"
    assert result["feishu"] is False
    assert delivered == []

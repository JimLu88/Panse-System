"""Exact custom-SKU selection and terminal no-sales quiet-exclusion gates."""
from datetime import datetime
from decimal import Decimal

from app.models.campaign import CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_price_floor_service
from app.services import campaign_recon_service as recon
from app.services import campaign_service as campaign
from app.services import no_sales_service


def _plan(db, remark: str = ""):
    plan = CampaignPlan(
        name="skip-rule-test",
        campaign_type="big88",
        tier="big",
        start_at=datetime(2026, 9, 10, 0, 0, 0),
        end_at=datetime(2026, 9, 12, 23, 59, 59),
        status="draft",
        remark=remark,
    )
    db.add(plan)
    db.commit()
    return plan


def _sku(db, *, product_code: str, sku_code: str, item_id: str, sku_id: str,
         placeholder: bool = False, alt_ids=None, daily=3000, big=2000,
         coupon_floor=None):
    db.add(PricingSku(
        product_code=product_code,
        sku_code=sku_code,
        sku=sku_code,
        product_name=product_code,
        daily_price=Decimal(str(daily)),
        is_custom_placeholder=placeholder,
    ))
    db.add(PricingSkuPromo(
        sku_code=sku_code,
        taobao_item_id=item_id,
        taobao_sku_id=sku_id,
        alt_taobao_sku_ids=list(alt_ids or []),
        big_buyer_price=Decimal(str(big)),
        coupon_floor_price=(
            Decimal(str(coupon_floor)) if coupon_floor is not None else None),
    ))
    db.commit()


def test_custom_placeholder_allowlist_parser_is_strict_and_item_id_does_not_spread(
        db_session):
    plan = _plan(db_session)
    assert campaign.custom_placeholder_sku_allowlist(plan) == set()
    for invalid in ("", "true", "881880805,true", "881880805，881880806"):
        plan.remark = f"custom_placeholder_sku_allowlist={invalid}"
        assert campaign.custom_placeholder_sku_allowlist(plan) == set()

    plan.remark = "custom_placeholder_sku_allowlist=881880805, 881880806"
    assert campaign.custom_placeholder_sku_allowlist(plan) == {
        "881880805", "881880806"}

    _sku(
        db_session, product_code="PPSCUST", sku_code="PPSCUST99",
        item_id="991880805", sku_id="881880805", alt_ids=["881880806"],
    )
    plan.remark = "custom_placeholder_sku_allowlist=991880805"
    signup, stats = campaign.build_signup_rows(db_session, plan)
    assert signup == []
    assert {
        row["taobao_sku_id"]
        for row in stats["excluded_unselected_custom_placeholder_skus"]
    } == {"881880805", "881880806"}


def test_exact_custom_sku_selection_does_not_spread_to_alt_id(db_session):
    plan = _plan(
        db_session,
        "custom_placeholder_sku_allowlist=881880805",
    )
    _sku(
        db_session, product_code="PPSCUST", sku_code="PPSCUST99",
        item_id="991880805", sku_id="881880805", alt_ids=["881880806"],
    )

    signup, signup_stats = campaign.build_signup_rows(db_session, plan)
    discounts, discount_stats = campaign.build_discount_rows(db_session, plan)
    targets = campaign.target_prices(db_session, plan)

    assert {row["taobao_sku_id"] for row in signup} == {"881880805"}
    assert {row["taobao_sku_id"] for row in discounts} == {"881880805"}
    assert set(targets) == {"881880805"}
    assert signup_stats["excluded_unselected_custom_placeholder_skus"][0][
        "taobao_sku_id"] == "881880806"
    assert discount_stats["excluded_unselected_custom_placeholder_skus"][0][
        "taobao_sku_id"] == "881880806"


def test_unselected_custom_and_placeholder_do_not_create_price_holds_or_guards(
        db_session):
    plan = _plan(db_session)
    _sku(
        db_session, product_code="PPSCUST", sku_code="PPSCUST99",
        item_id="991880805", sku_id="881880805",
    )
    _sku(
        db_session, product_code="PPSPLACE", sku_code="PPSPLACE99",
        item_id="991880806", sku_id="881880807", placeholder=True,
        daily=1000, big=700, coupon_floor=100,
    )
    campaign_price_floor_service.record_activity_export(
        db_session,
        [{
            "item_id": "991880805",
            "sku_id": "881880805",
            "min_list_price": 100,
            "min_coupon_line": 100,
        }],
        source="pytest_unselected_custom_conflict",
        plan=plan,
    )
    db_session.commit()

    analysis = campaign.price_resolution_analysis(db_session, plan)
    signup, stats = campaign.build_signup_rows(db_session, plan)
    targets = campaign.target_prices(db_session, plan)

    assert analysis["by_sku_id"] == {}
    assert analysis["holds"] == []
    assert signup == []
    assert stats["custom_floor_guard_items"] == []
    assert stats["placeholder_missing_live_price"] == []
    assert targets == {}


def test_exact_placeholder_allowlist_enables_only_named_sku(db_session):
    plan = _plan(
        db_session,
        "custom_placeholder_sku_allowlist=881880807; "
        "placeholder_live_prices=881880807:340",
    )
    _sku(
        db_session, product_code="PPSPLACE", sku_code="PPSPLACE98",
        item_id="991880806", sku_id="881880807", placeholder=True,
        daily=1000, big=700, coupon_floor=300,
    )
    _sku(
        db_session, product_code="PPSPLACE", sku_code="PPSPLACE99",
        item_id="991880806", sku_id="881880808", placeholder=True,
        daily=1000, big=700, coupon_floor=300,
    )

    signup, stats = campaign.build_signup_rows(db_session, plan)

    assert {row["taobao_sku_id"] for row in signup} == {"881880807"}
    assert stats["excluded_unselected_custom_placeholder_skus"] == [{
        "taobao_item_id": "991880806",
        "taobao_sku_id": "881880808",
        "sku_code": "PPSPLACE99",
        "is_placeholder": True,
    }]


def test_terminal_no_sales_is_absent_from_later_rows_holds_targets_and_recon(
        db_session):
    terminal_plan = _plan(db_session)
    item_id = "991880809"
    sku_id = "881880809"
    _sku(
        db_session, product_code="PPSNORM", sku_code="PPSNORM11",
        item_id=item_id, sku_id=sku_id,
    )
    terminal = campaign._classify_final_signup(
        db_session,
        terminal_plan,
        {
            "submitted": True,
            "validation": {
                "total_items": 1,
                "ok": 0,
                "failed": 1,
                "failed_items": [{
                    "item_id": item_id,
                    "reason": "动销不达标",
                    "raw": "近60天销售件数为0",
                }],
            },
        },
        [{"taobao_item_id": item_id, "taobao_sku_id": sku_id, "price": 3000}],
        set(),
    )
    assert terminal["ok"] is True
    assert terminal["no_sales_item_ids"] == [item_id]
    assert no_sales_service.get_no_sales(db_session) == {item_id}

    later_plan = _plan(db_session)
    signup, signup_stats = campaign.build_signup_rows(db_session, later_plan)
    discounts, discount_stats = campaign.build_discount_rows(db_session, later_plan)

    assert signup == []
    assert discounts == []
    assert campaign.price_hold_items(db_session, later_plan) == []
    assert campaign.target_prices(db_session, later_plan) == {}
    assert signup_stats["excluded_terminal_no_sales_items"] == [item_id]
    assert discount_stats["excluded_terminal_no_sales_items"] == [item_id]
    assert recon._compare_discounts(db_session, later_plan, [{
        "item_id": item_id,
        "sku_id": sku_id,
        "discount_value": 999,
    }]) == []


def test_local_zero_sales_observation_does_not_write_terminal_registry(db_session):
    _sku(
        db_session, product_code="PPSLOCAL", sku_code="PPSLOCAL11",
        item_id="991880810", sku_id="881880810",
    )

    grouping = campaign.group_by_sales(db_session)

    assert grouping["无动销"] == ["991880810"]
    assert grouping["newly_registered"] == ["991880810"]
    assert grouping["local_zero_sales_candidates"] == ["991880810"]
    assert grouping["registered"] == []
    assert no_sales_service.get_no_sales(db_session) == set()


def test_incomplete_platform_no_sales_result_does_not_write_registry(db_session):
    plan = _plan(db_session)
    item_id = "991880811"

    result = campaign._classify_final_signup(
        db_session,
        plan,
        {
            "submitted": True,
            "validation": {
                "total_items": 2,
                "ok": 1,
                "failed": 1,
                "failed_items": [{
                    "item_id": item_id,
                    "reason": "动销不达标",
                    "raw": "近60天销售件数为0",
                }],
            },
        },
        [{"taobao_item_id": item_id, "taobao_sku_id": "881880811"}],
        set(),
    )

    assert result["ok"] is False
    assert result["error"] == "signup_terminal_counts_invalid_or_scope_mismatch"
    assert no_sales_service.get_no_sales(db_session) == set()


def test_non_no_sales_platform_failure_remains_hard_error(
        db_session, monkeypatch):
    plan = _plan(db_session)
    plan.qn_campaign_title = "2026年9月安全门测试"
    plan.remark = "campaignId=59271; unitedActivityId=59283"
    item_id = "991880812"
    sku_id = "881880812"
    _sku(
        db_session, product_code="PPSHARD", sku_code="PPSHARD11",
        item_id=item_id, sku_id=sku_id,
    )
    monkeypatch.setattr(campaign, "preflight", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        campaign,
        "refresh_floor_evidence_from_current_activity",
        lambda *args, **kwargs: {
            "ok": True,
            "rows": [],
            "floor_refresh": {},
            "export_evidence": {
                "filename": "hard-failure.xlsx",
                "size": 1,
                "sha256": "a" * 64,
            },
        },
    )
    monkeypatch.setattr(
        campaign,
        "_refresh_official_product_sku_identity",
        lambda *args, **kwargs: {
            "ok": True,
            "artifact": {"filename": "product.xlsx", "size": 1,
                         "sha256": "b" * 64},
        },
    )
    monkeypatch.setattr(
        campaign,
        "_upload_and_wait",
        lambda *args, **kwargs: {
            "ok": False,
            "submitted": True,
            "platform_write_observed": True,
            "job": "hard-failure-job",
            "validation": {
                "total_items": 1,
                "ok": 0,
                "failed": 1,
                "failed_items": [{
                    "item_id": item_id,
                    "reason": "SKU已失效",
                    "raw": "SKU已失效",
                }],
            },
        },
    )

    result = campaign.push_signup(
        db_session, plan, execution_source="campaign_automation")

    assert result["ok"] is False
    assert result["step"] == "signup_hard_failures_isolated"
    assert result["hard_failed_item_ids"] == [item_id]
    assert result["terminal_classification"]["no_sales_item_ids"] == []
    assert plan.status == "alarmed"
    assert no_sales_service.get_no_sales(db_session) == set()

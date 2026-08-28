"""Regression gates for the ERP-only campaign preparation workflow."""
from datetime import datetime
from decimal import Decimal

from app.api.campaigns import (
    CampaignPrepareIn,
    _structured_prepare_remark,
    _validate_formal_platform_identity,
)
from app.models.campaign import CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_item_exclusion_service
from app.services import campaign_service as campaign
from app.services import campaign_workflow_service
from app.services import no_sales_service
from app.services import settings_service


def _pair(db, *, item_id: str, sku_code: str, sku_id: str,
          placeholder: bool = False):
    db.add(PricingSku(
        product_code=f"P{sku_code}", product_name=f"品{sku_code}",
        sku_code=sku_code, sku=f"规格{sku_code}",
        daily_price=Decimal("1200"), is_custom_placeholder=placeholder,
    ))
    db.add(PricingSkuPromo(
        sku_code=sku_code, taobao_item_id=item_id, taobao_sku_id=sku_id,
        big_buyer_price=Decimal("800"),
    ))


def _plan(**overrides):
    values = dict(
        name="9月准备", campaign_type="big88", tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="26年淘宝9月超级88",
        platform_activity_mode="fixed_window",
        platform_campaign_id="49462",
        platform_united_activity_id="49469",
        status="draft",
    )
    values.update(overrides)
    return CampaignPlan(**values)


def test_invalid_item_ids_never_enter_grouping_or_registry(db_session):
    for index, item_id in enumerate(("5", "待定", "暂无", "1234"), start=1):
        _pair(db_session, item_id=item_id, sku_code=f"S{index}", sku_id=f"K{index}")
    settings_service.set_value(
        db_session, "no_sales_item_ids", '["5", "待定", "暂无", "1234"]')
    db_session.commit()

    result = campaign.group_by_sales(db_session)

    assert result["invalid_item_ids_ignored"] == ["5", "待定", "暂无"]
    assert result["newly_registered"] == []
    assert result["registered"] == ["1234"]
    assert result["registry_cleanup"]["removed_invalid_values"] == ["5", "待定", "暂无"]


def test_no_sales_is_hard_excluded_from_signup(db_session):
    plan = _plan()
    db_session.add(plan)
    _pair(db_session, item_id="1000009209", sku_code="NS1", sku_id="SID1")
    db_session.commit()
    no_sales_service.add_no_sales(db_session, ["1000009209"])

    rows, stats = campaign.build_signup_rows(db_session, plan)

    assert rows == []
    assert stats["excluded_no_sales_items"] == ["1000009209"]


def test_whole_item_exclusion_never_keyword_guesses_mixed_link(db_session):
    plan = _plan(remark="placeholder_price_lowering_authorized=true")
    db_session.add(plan)
    _pair(db_session, item_id="792992319206", sku_code="REAL1", sku_id="R1")
    _pair(db_session, item_id="792992319206", sku_code="定制差价占位", sku_id="P1",
          placeholder=True)
    _pair(db_session, item_id="1001358847694", sku_code="ONLYP", sku_id="P2",
          placeholder=True)
    db_session.commit()

    exclusions = campaign.campaign_item_exclusions(db_session)
    rows, stats = campaign.build_signup_rows(db_session, plan)

    assert "792992319206" not in exclusions
    assert "1001358847694" in exclusions
    assert {row["taobao_item_id"] for row in rows} == {"792992319206"}
    assert {item["taobao_item_id"] for item in stats["excluded_whole_items"]} == {
        "1001358847694"}


def test_explicit_whole_item_exclusion_is_auditable(db_session):
    _pair(db_session, item_id="846844153512", sku_code="INSTALL1", sku_id="I1")
    db_session.commit()
    campaign_item_exclusion_service.upsert(
        db_session, item_id="846844153512",
        reason="运营确认该商品为安装专用链接")

    item = campaign.campaign_item_exclusions(db_session)["846844153512"]

    assert item["mode"] == "explicit_item_marker"
    assert item["reason"] == "运营确认该商品为安装专用链接"


def test_plan_scoped_official_exemption_generates_no_signup_or_discount_rows(
        db_session):
    plan = _plan(
        campaign_type="super_reduce", tier="mid",
        platform_activity_mode="long_running_update",
        platform_campaign_id=None,
        platform_united_activity_id=None,
        platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
        remark=(
            "official_all_store=true; "
            "official_exempt_items=805268708396"
        ))
    db_session.add(plan)
    _pair(
        db_session, item_id="805268708396",
        sku_code="CUSTOMBOOKCASE", sku_id="80526870839601")
    _pair(
        db_session, item_id="805268708397",
        sku_code="NORMAL", sku_id="80526870839701")
    db_session.commit()

    signup_rows, signup_stats = campaign.build_signup_rows(db_session, plan)
    discount_rows, discount_stats = campaign.build_discount_rows(db_session, plan)

    assert {row["taobao_item_id"] for row in signup_rows} == {"805268708397"}
    assert {row["taobao_item_id"] for row in discount_rows} == {"805268708397"}
    assert signup_stats["excluded_official_exempt_items"] == ["805268708396"]
    assert discount_stats["excluded_official_exempt_items"] == ["805268708396"]


def test_plan_scoped_official_exemption_correction_is_cas_and_idempotent(
        db_session):
    plan = _plan(
        workflow_key="campaign:super-reduce:2026-09-01",
        name="2026-09-01超级立减更新窗口",
        campaign_type="super_reduce", tier="mid",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 1, 23, 59, 59),
        qn_campaign_title="超级立减",
        platform_activity_mode="long_running_update",
        platform_campaign_id=None,
        platform_united_activity_id=None,
        platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
        remark="official_all_store=true; official_exempt_items=",
    )
    db_session.add(plan)
    db_session.commit()

    changed = campaign_workflow_service.correct_official_exemptions(
        db_session,
        workflow_key=plan.workflow_key,
        expected_plan_id=plan.id,
        expected_item_ids=[],
        desired_item_ids=["805268708396"],
    )
    replay = campaign_workflow_service.correct_official_exemptions(
        db_session,
        workflow_key=plan.workflow_key,
        expected_plan_id=plan.id,
        expected_item_ids=[],
        desired_item_ids=["805268708396"],
    )
    compare_failed = campaign_workflow_service.correct_official_exemptions(
        db_session,
        workflow_key=plan.workflow_key,
        expected_plan_id=plan.id,
        expected_item_ids=[],
        desired_item_ids=["805268708397"],
    )

    assert changed["changed"] is True
    assert changed["plan"].remark == (
        "official_all_store=true; official_exempt_items=805268708396")
    assert replay["changed"] is False
    assert replay["idempotent_replay"] is True
    assert compare_failed["error"] == "official_exemptions_compare_failed"
    assert changed["execution_boundary"] == {
        "plan_scoped_only": True,
        "permanent_exclusion_write": False,
        "platform_write": False,
        "account_action": False,
        "notification": False,
        "automatic_retry": False,
    }

    plan.status = "signup_pushed"
    db_session.commit()
    submitted = campaign_workflow_service.correct_official_exemptions(
        db_session,
        workflow_key=plan.workflow_key,
        expected_plan_id=plan.id,
        expected_item_ids=["805268708396"],
        desired_item_ids=[],
    )
    assert submitted["error"] == "unsubmitted_plan_required"


def test_long_running_super_reduce_has_typed_identity_without_fake_short_activity():
    body = CampaignPrepareIn(
        workflow_key="campaign:super-reduce:2026-09-01",
        name="2026-09-01超级立减更新窗口",
        campaign_type="super_reduce",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 1, 23, 59, 59),
        qn_campaign_title="超级立减",
        platform_activity_mode="long_running_update",
        platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
    )

    _validate_formal_platform_identity(body)
    plan = _plan(
        name=body.name, campaign_type="super_reduce", tier="mid",
        start_at=body.start_at, end_at=body.end_at,
        qn_campaign_title=body.qn_campaign_title,
        platform_activity_mode=body.platform_activity_mode,
        platform_campaign_id=None, platform_united_activity_id=None,
        platform_active_until=body.platform_active_until,
    )
    identity = campaign.campaign_identity(plan)

    assert identity["ok"] is True
    assert identity["platform_activity_mode"] == "long_running_update"
    assert identity["platform_active_until"] == "2028-07-31 23:59:59"


def test_prepare_accepts_structured_official_scope_without_free_text_markers():
    body = CampaignPrepareIn(
        workflow_key="campaign:super-reduce:2026-09-01",
        name="2026-09-01超级立减更新窗口",
        campaign_type="super_reduce",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 1, 23, 59, 59),
        qn_campaign_title="超级立减",
        platform_activity_mode="long_running_update",
        platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
        official_all_store=True,
        official_exempt_item_ids=["1000009209"],
    )

    assert _structured_prepare_remark(body) == (
        "official_all_store=true; official_exempt_items=1000009209")


def test_prepare_preserves_explicit_empty_exempt_scope_but_not_omission():
    common = dict(
        workflow_key="campaign:super-reduce:2026-09-01",
        name="2026-09-01超级立减更新窗口",
        campaign_type="super_reduce",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 1, 23, 59, 59),
        qn_campaign_title="超级立减",
        platform_activity_mode="long_running_update",
        platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
        official_all_store=True,
    )

    omitted = CampaignPrepareIn(**common)
    explicit_empty = CampaignPrepareIn(
        **common, official_exempt_item_ids=[])

    assert _structured_prepare_remark(omitted) == "official_all_store=true"
    assert _structured_prepare_remark(explicit_empty) == (
        "official_all_store=true; official_exempt_items=")


def test_empty_exempt_enrichment_reuses_long_and_fixed_workflows(db_session):
    cases = [
        dict(
            workflow_key="campaign:super-reduce:2026-09-01",
            name="2026-09-01超级立减更新窗口",
            campaign_type="super_reduce", tier="mid",
            start_at=datetime(2026, 9, 1, 0, 0, 0),
            end_at=datetime(2026, 9, 1, 23, 59, 59),
            qn_campaign_title="超级立减",
            platform_activity_mode="long_running_update",
            platform_campaign_id=None, platform_united_activity_id=None,
            platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
        ),
        dict(
            workflow_key="campaign:super88:49462:49469",
            name="超级88现货", campaign_type="big88", tier="big",
            start_at=datetime(2026, 9, 6, 20, 0, 0),
            end_at=datetime(2026, 9, 13, 23, 59, 59),
            qn_campaign_title="26年淘宝9月超级88",
            platform_activity_mode="fixed_window",
            platform_campaign_id="49462", platform_united_activity_id="49469",
            platform_active_until=None,
        ),
    ]
    for case in cases:
        workflow_key = case.pop("workflow_key")
        plan = _plan(
            **case, workflow_key=workflow_key,
            remark="official_all_store=true")
        db_session.add(plan)
        db_session.commit()
        original_id = plan.id
        values = {
            **case,
            "price_protection_days": 19,
            "price_protection_rule_url": None,
            "price_protection_confirmed_at": None,
            "remark": "official_all_store=true; official_exempt_items=",
        }

        repaired = campaign_workflow_service.prepare(
            db_session, workflow_key=workflow_key, values=values)
        repeated = campaign_workflow_service.prepare(
            db_session, workflow_key=workflow_key, values=values)
        changed_scope = campaign_workflow_service.prepare(
            db_session, workflow_key=workflow_key,
            values={
                **values,
                "remark": (
                    "official_all_store=true; "
                    "official_exempt_items=1000009209"),
            })
        r15 = next(
            check for check in repaired["preflight"]["checks"]
            if check["rule"] == "R15")

        assert repaired.get("conflict") is not True
        assert repaired["created"] is False
        assert repaired["reused"] is True
        assert repaired["repaired_fields"] == ["remark"]
        assert repaired["plan"].id == original_id
        assert repaired["plan"].remark.endswith("official_exempt_items=")
        assert r15["level"] != "error"
        assert repeated["repaired_fields"] == []
        assert repeated["plan"].id == original_id
        assert changed_scope["conflict"] is True
        assert changed_scope["different_fields"] == ["remark"]


def test_prepare_workflow_key_is_durable_and_payload_conflicts(db_session):
    values = dict(
        name="26年淘宝9月超级88", campaign_type="big88", tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="26年淘宝9月超级88", price_protection_days=19,
        price_protection_rule_url=None, price_protection_confirmed_at=None,
        remark=None, platform_activity_mode="fixed_window",
        platform_campaign_id="49462", platform_united_activity_id="49469",
        platform_active_until=None,
    )

    first = campaign_workflow_service.prepare(
        db_session, workflow_key="campaign:super88:49462:49469", values=values)
    second = campaign_workflow_service.prepare(
        db_session, workflow_key="campaign:super88:49462:49469", values=values)
    conflict = campaign_workflow_service.prepare(
        db_session, workflow_key="campaign:super88:49462:49469",
        values={**values, "platform_campaign_id": "99999"})

    assert first["created"] is True
    assert second["reused"] is True
    assert second["plan"].id == first["plan"].id
    assert conflict["conflict"] is True
    assert conflict["different_fields"] == ["platform_campaign_id"]
    assert first["execution_boundary"]["platform_write"] is False


def test_prepare_one_way_enriches_numeric_sign_record_identity(db_session):
    values = dict(
        name="超级88现货", campaign_type="big88", tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="26年淘宝9月超级88", price_protection_days=19,
        price_protection_rule_url=None, price_protection_confirmed_at=None,
        remark="official_all_store=true; official_exempt_items=",
        platform_activity_mode="fixed_window",
        platform_campaign_id="49462", platform_united_activity_id="49469",
        platform_sign_record_id=None, platform_active_until=None,
    )
    first = campaign_workflow_service.prepare(
        db_session, workflow_key="campaign:super88:49462:49469", values=values)
    enriched = campaign_workflow_service.prepare(
        db_session, workflow_key="campaign:super88:49462:49469",
        values={**values, "platform_sign_record_id": "3527841611"})
    changed_again = campaign_workflow_service.prepare(
        db_session, workflow_key="campaign:super88:49462:49469",
        values={**values, "platform_sign_record_id": "3527841612"})

    assert first["created"] is True
    assert enriched["repaired_fields"] == ["platform_sign_record_id"]
    assert enriched["plan"].platform_sign_record_id == "3527841611"
    assert changed_again["conflict"] is True
    assert changed_again["different_fields"] == ["platform_sign_record_id"]


def test_rotation_marker_is_r7_hard_error(db_session):
    plan = _plan(remark="sku_refresh_items_authorized=1000009219")
    db_session.add(plan)
    db_session.commit()

    checks = {check["rule"]: check for check in campaign.preflight(db_session, plan)}

    assert checks["R7"]["level"] == "error"
    assert checks["R7"]["items"] == ["1000009219"]

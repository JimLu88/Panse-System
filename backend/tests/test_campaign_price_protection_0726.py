"""活动价保方案3加强版：默认19天、逐场可改、缺链接飞书提醒、价格线冲突整品暂缓。"""
from datetime import datetime, timedelta
from decimal import Decimal

from app.models.campaign import CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_price_protection_service as pps
from app.services import campaign_service
from app.services import delisted_sku_service


def _seed_platform_floors(db, rows):
    from app.services import campaign_price_floor_service

    campaign_price_floor_service.record_activity_export(
        db,
        [{
            "item_id": str(item_id),
            "sku_id": str(sku_id),
            "sku_name": str(sku_id),
            "min_list_price": min_list,
            "min_coupon_line": min_coupon,
        } for item_id, sku_id, min_list, min_coupon in rows],
        source="pytest_current_activity_export",
    )
    db.commit()


def _plan(db, *, days=19, url=None):
    plan = CampaignPlan(
        name="双11测试活动", campaign_type="big11", tier="big618",
        start_at=datetime.now() + timedelta(days=3),
        end_at=datetime.now() + timedelta(days=8),
        qn_campaign_title="2026双11",
        price_protection_days=days,
        price_protection_rule_url=url,
        status="draft",
    )
    db.add(plan)
    db.commit()
    return plan


def _super_reduce_plan(db):
    plan = CampaignPlan(
        name="超级立减资格门测试", campaign_type="super_reduce", tier="mid",
        start_at=datetime.now() + timedelta(days=3),
        end_at=datetime.now() + timedelta(days=8),
        qn_campaign_title="超级立减长期活动",
        price_protection_days=19,
        status="draft",
    )
    db.add(plan)
    db.commit()
    return plan


def test_default_and_manual_price_protection_days(db_session):
    plan = _plan(db_session)
    assert pps.protection_days(plan) == 19
    assert pps.protection_until(plan) == plan.end_at + timedelta(days=19)
    assert pps.rule_check(plan)["level"] == "warn"

    plan.price_protection_days = 7
    plan.price_protection_rule_url = "https://rules.example/activity"
    plan.price_protection_confirmed_at = datetime.now()
    db_session.commit()
    check = pps.rule_check(plan)
    assert check["level"] == "pass"
    assert check["items"][0]["days"] == 7
    assert check["items"][0]["default_used"] is False


def test_missing_rule_link_notice_is_deduped_after_delivery(db_session, monkeypatch):
    plan = _plan(db_session)
    calls = []
    from app.services import notify_service
    monkeypatch.setattr(
        notify_service, "broadcast_text",
        lambda db, text, **kwargs: calls.append((text, kwargs)) or {"feishu": True})

    first = pps.notify_rule_link_needed(db_session, plan)
    second = pps.notify_rule_link_needed(db_session, plan)

    assert first["sent"] is True
    assert second["deduped"] is True
    assert len(calls) == 1
    assert "19天" in calls[0][0] and "价保说明" in calls[0][0]


def test_sub_two_yuan_history_conflict_is_adjusted_without_holding_item(db_session):
    plan = _plan(db_session)
    for code, item, sid, daily, big, line in (
        ("PPSHOLD01", "9901", "88001", 3000, 2000, 1999.99),
        ("PPSSAFE01", "9902", "88002", 3000, 2000, 2000),
    ):
        db_session.add(PricingSku(
            product_code=code[:-2], sku_code=code, sku="1.6米",
            product_name=code, daily_price=Decimal(str(daily))))
        db_session.add(PricingSkuPromo(
            sku_code=code, taobao_item_id=item, taobao_sku_id=sid,
            big_buyer_price=Decimal(str(big)),
            coupon_floor_price=Decimal(str(line))))
    db_session.commit()
    _seed_platform_floors(db_session, [
        ("9901", "88001", 4000, 1999.99),
        ("9902", "88002", 4000, 2000),
    ])

    holds = campaign_service.price_hold_items(db_session, plan)
    signup, signup_stats = campaign_service.build_signup_rows(db_session, plan)
    discounts, discount_stats = campaign_service.build_discount_rows(db_session, plan)

    assert holds == []
    assert {x["taobao_item_id"] for x in signup} == {"9901", "9902"}
    assert {x["taobao_item_id"] for x in discounts} == {"9901", "9902"}
    assert signup_stats["excluded_price_hold_items"] == []
    assert discount_stats["excluded_price_hold_items"] == []
    adjusted = next(x for x in signup if x["taobao_item_id"] == "9901")
    assert adjusted["price"] == 3000.0
    assert signup_stats["automatic_price_adjustments"]


def test_delisted_rotated_sku_floor_does_not_hold_replacement_item(db_session):
    plan = _plan(db_session)
    db_session.add_all([
        PricingSku(
            product_code="PPSROTATE", sku_code="PPSROTATE_OLD", sku="旧配件",
            product_name="轮换商品", daily_price=Decimal("400")),
        PricingSkuPromo(
            sku_code="PPSROTATE_OLD", taobao_item_id="9903",
            taobao_sku_id="88003", big_buyer_price=Decimal("250"),
            coupon_floor_price=Decimal("240")),
        PricingSku(
            product_code="PPSROTATE", sku_code="PPSROTATE_NEW", sku="新主体",
            product_name="轮换商品", daily_price=Decimal("3000")),
        PricingSkuPromo(
            sku_code="PPSROTATE_NEW", taobao_item_id="9903",
            taobao_sku_id="88004", big_buyer_price=Decimal("2000"),
            coupon_floor_price=Decimal("2000")),
    ])
    db_session.commit()
    delisted_sku_service.add_delisted(db_session, ["88003"])
    _seed_platform_floors(db_session, [
        ("9903", "88003", 400, 200),
        ("9903", "88004", 3000, 2000),
    ])

    holds = campaign_service.price_hold_items(db_session, plan)
    signup, stats = campaign_service.build_signup_rows(db_session, plan)

    assert holds == []
    assert [row["taobao_sku_id"] for row in signup] == ["88004"]
    assert stats["excluded_price_hold_items"] == []


def test_coupon_floor_gate_counts_concurrent_single_item_discount(db_session):
    """平台导出说明：最低券后价会计入店铺其它优惠，最终21元可通过24.01元线。"""
    plan = _super_reduce_plan(db_session)
    sample_item = "719436834260"
    for index, (sid, floor) in enumerate((
        ("6278611018580", "24.01"),
        ("6278614802119", "24.02"),
        ("6278875759312", "24.01"),
    ), start=1):
        code = f"PPS_SAMPLE_{index}"
        db_session.add(PricingSku(
            product_code="PPS_SAMPLE", sku_code=code, sku=f"样块{index}",
            product_name="榉木样块", daily_price=Decimal("30")))
        db_session.add(PricingSkuPromo(
            sku_code=code, taobao_item_id=sample_item, taobao_sku_id=sid,
            big_buyer_price=Decimal("20.39"),
            coupon_floor_price=Decimal(floor)))

    db_session.add(PricingSku(
        product_code="PPS_SAFE", sku_code="PPS_SAFE_1", sku="安全样块",
        product_name="安全样块", daily_price=Decimal("30")))
    db_session.add(PricingSkuPromo(
        sku_code="PPS_SAFE_1", taobao_item_id="719436834261",
        taobao_sku_id="6278875759313", big_buyer_price=Decimal("20.39"),
        coupon_floor_price=Decimal("27.00")))
    db_session.commit()
    _seed_platform_floors(db_session, [
        (sample_item, "6278611018580", 30, 24.01),
        (sample_item, "6278614802119", 30, 24.02),
        (sample_item, "6278875759312", 30, 24.01),
        ("719436834261", "6278875759313", 30, 27.00),
    ])

    holds = campaign_service.price_hold_items(db_session, plan)
    signup, signup_stats = campaign_service.build_signup_rows(db_session, plan)
    discounts, discount_stats = campaign_service.build_discount_rows(db_session, plan)
    checks = {x["rule"]: x for x in campaign_service.preflight(db_session, plan)}

    assert holds == []
    assert {row["taobao_item_id"] for row in signup} == {sample_item, "719436834261"}
    assert {row["taobao_item_id"] for row in discounts} == {sample_item, "719436834261"}
    assert signup_stats["excluded_price_hold_items"] == []
    assert discount_stats["excluded_price_hold_items"] == []
    assert checks["R2"]["level"] == "pass"
    assert "2元内价格冲突自动合并修正" in checks["R2"]["title"]


def test_price_math_gate_detects_no_difference(db_session):
    plan = _plan(db_session)
    db_session.add(PricingSku(
        product_code="PPSMATH", sku_code="PPSMATH01", sku="1.6米",
        product_name="验算桌", daily_price=Decimal("3000")))
    db_session.add(PricingSkuPromo(
        sku_code="PPSMATH01", taobao_item_id="9910", taobao_sku_id="88100",
        big_buyer_price=Decimal("2000"), coupon_floor_price=Decimal("2550")))
    db_session.commit()

    checks = {x["rule"]: x for x in campaign_service.preflight(db_session, plan)}
    assert checks["R13"]["level"] == "pass"
    assert checks["R13"]["checked"] == {"signup_rows": 1, "discount_rows": 1}

"""活动价保方案3加强版：默认19天、逐场可改、缺链接飞书提醒、价格线冲突整品暂缓。"""
from datetime import datetime, timedelta
from decimal import Decimal

from app.models.campaign import CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_price_protection_service as pps
from app.services import campaign_service


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


def test_history_line_conflict_holds_whole_item_but_not_other_item(db_session):
    plan = _plan(db_session)
    for code, item, sid, daily, big, line in (
        ("PPSHOLD01", "9901", "88001", 3000, 2000, 1900),
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

    holds = campaign_service.price_hold_items(db_session, plan)
    signup, signup_stats = campaign_service.build_signup_rows(db_session, plan)
    discounts, discount_stats = campaign_service.build_discount_rows(db_session, plan)

    assert [x["taobao_item_id"] for x in holds] == ["9901"]
    assert {x["taobao_item_id"] for x in signup} == {"9902"}
    assert {x["taobao_item_id"] for x in discounts} == {"9902"}
    assert signup_stats["excluded_price_hold_items"][0]["taobao_item_id"] == "9901"
    assert discount_stats["excluded_price_hold_items"][0]["taobao_item_id"] == "9901"


def test_price_math_gate_detects_no_difference(db_session):
    plan = _plan(db_session)
    db_session.add(PricingSku(
        product_code="PPSMATH", sku_code="PPSMATH01", sku="1.6米",
        product_name="验算桌", daily_price=Decimal("3000")))
    db_session.add(PricingSkuPromo(
        sku_code="PPSMATH01", taobao_item_id="9910", taobao_sku_id="88100",
        big_buyer_price=Decimal("2000"), coupon_floor_price=Decimal("2000")))
    db_session.commit()

    checks = {x["rule"]: x for x in campaign_service.preflight(db_session, plan)}
    assert checks["R13"]["level"] == "pass"
    assert checks["R13"]["checked"] == {"signup_rows": 1, "discount_rows": 1}

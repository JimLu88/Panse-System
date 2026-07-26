"""超大促 SKU 轮换生成器 (价格体系设置.md §九, 2026-07-11)。
验证: 标签整体下移一位 + 商家编码永远跟尺寸走(只 skuId 映射轮转)。"""
from decimal import Decimal

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import sku_rotation_service as svc
from app.services import settings_service


def _seed(db):
    # 一条阶梯: 榉木床 2.1/1.8/1.5/1.2 (降序), 各带 skuId; + 1 个定制 buffer
    ladder = [("PR000121", "榉木床-2.1米", 8000, "S21"),
              ("PR000122", "榉木床-1.8米", 7000, "S18"),
              ("PR000123", "榉木床-1.5米", 6000, "S15"),
              ("PR000124", "榉木床-1.2米", 5000, "S12")]
    for sc, name, price, skuid in ladder:
        db.add(PricingSku(product_code="PR0001", sku_code=sc, sku=name, daily_price=Decimal(str(price))))
        db.add(PricingSkuPromo(
            sku_code=sc, taobao_item_id="900", taobao_sku_id=skuid,
            coupon_floor_price=Decimal("123.45"),
            enrolled_floor_price=Decimal("234.56")))
    db.add(PricingSku(product_code="PR0001", sku_code="PR000199", sku="榉木 尺寸定制",
                      daily_price=Decimal("1000"), is_custom_placeholder=True))
    db.add(PricingSkuPromo(sku_code="PR000199", taobao_item_id="900", taobao_sku_id="SBUF"))
    db.commit()


def test_rotation_shifts_labels_down(db_session):
    db = db_session
    _seed(db)
    settings_service.set_value(db, "promo_sku_rotation_enabled", "1")
    db.commit()
    plan = svc.plan_rotation(db, "PR0001")
    assert plan["ok"]
    lad = plan["ladders"][0]
    assert not lad["warnings"], lad["warnings"]
    m = {r["sku_code"]: r["new_skuId"] for r in lad["erp_mapping"]}
    # 商家编码跟尺寸: 2.1米编码 → 落到 buffer 的老 skuId(干净线); 1.8米编码 → 落到 2.1 的老槽
    assert m["PR000121"] == "SBUF"    # 2.1米 → buffer槽(SBUF)
    assert m["PR000122"] == "S21"     # 1.8米 → 原2.1槽
    assert m["PR000123"] == "S18"     # 1.5米 → 原1.8槽
    assert m["PR000124"] == "S15"     # 1.2米 → 原1.5槽
    assert m["PR000199"] == "S12"     # 定制buffer → 原1.2槽(轮到底休眠)


def test_apply_mapping_dry_run_then_real(db_session):
    db = db_session
    _seed(db)
    settings_service.set_value(db, "promo_sku_rotation_enabled", "1")
    db.commit()
    plan = svc.plan_rotation(db, "PR0001")
    em = plan["ladders"][0]["erp_mapping"]
    dry = svc.apply_mapping(db, "PR0001", em, dry_run=True)
    assert dry["changed"] == 5 and dry["dry_run"]
    from sqlalchemy import select
    p = db.execute(select(PricingSkuPromo).where(PricingSkuPromo.sku_code == "PR000121")).scalar_one()
    assert p.taobao_sku_id == "S21"
    real = svc.apply_mapping(db, "PR0001", em, dry_run=False)
    assert real["changed"] == 5 and not real["dry_run"]
    db.expire_all()
    p = db.execute(select(PricingSkuPromo).where(PricingSkuPromo.sku_code == "PR000121")).scalar_one()
    assert p.taobao_sku_id == "SBUF"   # 落库后 2.1米编码 → buffer槽
    assert p.coupon_floor_price is None
    assert p.enrolled_floor_price is None


def test_rotation_is_blocked_by_default(db_session):
    _seed(db_session)
    result = svc.plan_rotation(db_session, "PR0001")
    assert result["ok"] is False
    assert result["blocked_by"] == "price_protection_policy"
    assert "默认关闭" in result["error"]

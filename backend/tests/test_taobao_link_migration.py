from io import BytesIO
from decimal import Decimal

from openpyxl import Workbook

from app.models.bom import BomLine
from app.models.campaign import CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.product import Product
from app.models.taobao_listing import TaobaoListing
from app.services import taobao_link_migration_service as svc


PC = "PPS26330100225"
OLD_ITEM = "1038064128030"
NEW_ITEM = "1074244132390"


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["说明"])
    ws.append(["商品信息"])
    ws.append([
        "商品Id", "类目id", "类目名称", "宝贝标题", "一口价", "导购标题",
        "商家编码", "发货时间", "最长发货时间", "销售属性", "属性对",
        "skuId", "价格(元)", "库存(件)", "发货时间", "商家编码",
    ])
    ws.append([
        NEW_ITEM, "cat", "实木床", "新床链接", "1000", None, None, "30", "50",
        "材质:榉木;尺寸:1.2米;", None, "NEW-SID-1", "7450", "100", None,
        f"{PC}11",
    ])
    # 第二行故意缺 SKU 商家编码：流程必须借旧链接相同规格 + 旧 promo skuId 唯一恢复。
    ws.append([
        NEW_ITEM, "cat", "实木床", "新床链接", "1000", None, None, "30", "50",
        "材质:松木;尺寸:1.2米;", None, "NEW-SID-2", "6420", "100", None, None,
    ])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _seed(db):
    product = Product(code=PC, name="樱桃木齐边床", taobao_id=OLD_ITEM, alt_taobao_ids=[])
    db.add(product)
    for suffix, name, old_sid in [
        ("11", "榉木1.2米", "OLD-SID-1"),
        ("12", "松木1.2米", "OLD-SID-2"),
    ]:
        code = f"{PC}{suffix}"
        db.add(PricingSku(
            product_code=PC, product_name=product.name, sku_code=code, sku=name,
            daily_price=Decimal("5000"),
        ))
        db.add(PricingSkuPromo(
            sku_code=code, taobao_item_id=OLD_ITEM,
            taobao_url=svc.canonical_taobao_url(OLD_ITEM), taobao_sku_id=old_sid,
            alt_taobao_sku_ids=[], enrolled_floor_price=Decimal("4900"),
            coupon_floor_price=Decimal("4800"),
        ))
    db.add(TaobaoListing(
        taobao_item_id=OLD_ITEM, taobao_sku_id="OLD-SID-1", title="旧床链接",
        sku_spec="材质:榉木;尺寸:1.2米;", sku_code=f"{PC}11", product_code=PC,
        matched=True, shop="畔色店",
    ))
    db.add(TaobaoListing(
        taobao_item_id=OLD_ITEM, taobao_sku_id="OLD-SID-2", title="旧床链接",
        sku_spec="材质:松木;尺寸:1.2米;", sku_code=None, product_code=PC,
        matched=True, shop="畔色店",
    ))
    db.add(BomLine(product_code=PC, sku_code=f"{PC}11", material_code="WD-1", qty_per_product=1))
    db.commit()
    return product


def test_add_preserves_old_primary_campaign_and_bom(db_session):
    product = _seed(db_session)
    before_bom = db_session.query(BomLine).count()

    preview = svc.preview(db_session, _xlsx(), product_code=PC, mode="add")
    assert preview["sku_count"] == 2
    assert preview["effects"] == {
        "product_primary_changes": False,
        "product_alias_added": True,
        "campaign_mapping_changes": 0,
        "bom_changes": 0,
        "historical_order_changes": 0,
    }
    result = svc.apply(db_session, _xlsx(), product_code=PC, mode="add", shop="畔色店")

    db_session.refresh(product)
    assert result["inserted"] == 2
    assert result["matched"] == 2
    assert product.taobao_id == OLD_ITEM
    assert product.alt_taobao_ids == [NEW_ITEM]
    assert db_session.query(BomLine).count() == before_bom
    promos = db_session.query(PricingSkuPromo).order_by(PricingSkuPromo.sku_code).all()
    assert {p.taobao_item_id for p in promos} == {OLD_ITEM}
    assert {p.taobao_sku_id for p in promos} == {"OLD-SID-1", "OLD-SID-2"}
    rows = db_session.query(TaobaoListing).filter_by(taobao_item_id=NEW_ITEM).all()
    assert {(r.taobao_sku_id, r.sku_code) for r in rows} == {
        ("NEW-SID-1", f"{PC}11"), ("NEW-SID-2", f"{PC}12")
    }

    second = svc.apply(db_session, _xlsx(), product_code=PC, mode="add", shop="畔色店")
    db_session.refresh(product)
    assert second["inserted"] == 0
    assert second["updated"] == 2
    assert product.alt_taobao_ids == [NEW_ITEM]
    assert db_session.query(TaobaoListing).filter_by(taobao_item_id=NEW_ITEM).count() == 2


def test_activate_switches_current_mapping_but_keeps_old_history(db_session):
    product = _seed(db_session)
    svc.apply(db_session, _xlsx(), product_code=PC, mode="add", shop="畔色店")
    result = svc.apply(db_session, _xlsx(), product_code=PC, mode="activate", shop="畔色店")

    db_session.refresh(product)
    assert result["effects"]["campaign_mapping_changes"] == 2
    assert product.taobao_id == NEW_ITEM
    assert product.alt_taobao_ids == [OLD_ITEM]
    promos = db_session.query(PricingSkuPromo).order_by(PricingSkuPromo.sku_code).all()
    assert {p.taobao_item_id for p in promos} == {NEW_ITEM}
    assert {p.taobao_sku_id for p in promos} == {"NEW-SID-1", "NEW-SID-2"}
    assert all(p.taobao_url == svc.canonical_taobao_url(NEW_ITEM) for p in promos)
    assert all(p.alt_taobao_sku_ids == [] for p in promos)
    assert all(p.enrolled_floor_price is None and p.coupon_floor_price is None for p in promos)
    assert db_session.query(TaobaoListing).filter_by(taobao_item_id=OLD_ITEM).count() == 2
    assert db_session.query(TaobaoListing).filter_by(taobao_item_id=NEW_ITEM).count() == 2


def test_activate_blocks_open_campaign_plan(db_session):
    _seed(db_session)
    db_session.add(CampaignPlan(name="未完成活动", campaign_type="big88", tier="big", status="precheck"))
    db_session.commit()

    try:
        svc.preview(db_session, _xlsx(), product_code=PC, mode="activate")
    except ValueError as exc:
        assert "存在未收口活动计划" in str(exc)
    else:
        raise AssertionError("activation should be blocked")

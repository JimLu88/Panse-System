"""报名可行性三件套 (2026-07-12 第二场62件全失败复盘):
① 占位SKU报名价 = ×0.9 封顶到 enrolled_floor_price(已生效活动价=淘宝券后价硬底);
② 整商品完整性: 任一已映射SKU缺价 → 整商品剔除(淘宝要求全SKU报名, 半套必拒);
③ 预检: 券后价超线红字(planned>floor) / incomplete_items / 近60天0销量警示。
"""
from decimal import Decimal

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import activity_preflight_service as pf
from app.services import data_export_service as de
from app.services.activity_upload_service import _compare_rows, _parse_uploaded_values


def _add(db, pc, sc, name, daily, item, sid, big_buyer=None, mid_buyer=None, ph=False, floor=None):
    db.add(PricingSku(product_code=pc, sku_code=sc, sku=name, product_name=name,
                      daily_price=Decimal(str(daily)) if daily is not None else None,
                      is_custom_placeholder=ph))
    db.add(PricingSkuPromo(sku_code=sc, taobao_item_id=item, taobao_sku_id=sid,
                           big_buyer_price=Decimal(str(big_buyer)) if big_buyer else None,
                           mid_buyer_price=Decimal(str(mid_buyer)) if mid_buyer else None,
                           enrolled_floor_price=Decimal(str(floor)) if floor else None))


def test_placeholder_capped_to_enrolled_floor(db_session):
    db = db_session
    # 完整商品: 正常SKU + 占位SKU(daily 1500×0.9=1350, 但已生效价400 → 封顶400)
    _add(db, "PPS9101", "PPS9101011", "书柜-1.0米", 6470, "111", "800001", big_buyer=4900, mid_buyer=5010)
    _add(db, "PPS9101", "PPS9101099", "尺寸定制", 1500, "111", "800099", ph=True, floor=400)
    db.commit()
    entries, stats = de.collect_signup_rows(db, "report_price")
    price = {s.sku_code: A for s, _p, A in entries}
    assert price["PPS9101099"] == 400.0          # 封顶到已生效价
    assert stats["skipped_incomplete_items"] == 0
    # 无底价时维持 ×0.9 老口径
    _add(db, "PPS9102", "PPS9102011", "餐桌-1.4米", 3000, "222", "800011", big_buyer=2300, mid_buyer=2352)
    _add(db, "PPS9102", "PPS9102099", "尺寸定制B", 1500, "222", "800098", ph=True)
    db.commit()
    entries, _ = de.collect_signup_rows(db, "report_price")
    price = {s.sku_code: A for s, _p, A in entries}
    assert price["PPS9102099"] == 1350.0


def test_incomplete_item_dropped_whole(db_session):
    db = db_session
    # 商品333: 一个有价 + 一个无价(无big_buyer非占位) → 整商品剔除
    _add(db, "PPS9201", "PPS9201011", "床-1.5米", 2600, "333", "800021", big_buyer=2000, mid_buyer=2046)
    _add(db, "PPS9201", "PPS9201097", "追加背板", 2000, "333", "800022")          # 无促销价→缺价
    # 商品444: 全有价 → 保留
    _add(db, "PPS9202", "PPS9202011", "柜-0.6米", 3600, "444", "800031", big_buyer=2204, mid_buyer=2254)
    db.commit()
    entries, stats = de.collect_signup_rows(db, "report_price")
    codes = {s.sku_code for s, _p, _A in entries}
    assert "PPS9201011" not in codes and "PPS9201097" not in codes   # 整商品剔除(含有价的那个SKU)
    assert "PPS9202011" in codes
    assert stats["skipped_incomplete_items"] == 1
    assert stats["incomplete_items"][0]["taobao_item_id"] == "333"
    assert any("PPS9201097" in m for m in stats["incomplete_items"][0]["missing_skus"])
    # builder 与比对表行集一致(零漂移含剔除口径)
    bio, _ = de.build_promo_signup_upload_xlsx(db, "big")
    xlsx_ids = set(_parse_uploaded_values("promo_signup", bio.getvalue()))
    cmp_ids = {r["taobao_sku_id"] for r in _compare_rows(db, "promo_signup", "big")}
    assert xlsx_ids == cmp_ids


def test_preflight_floor_and_no_sales(db_session):
    db = db_session
    # 正常SKU 报名价(2000/0.88≈2273) > 已生效价1800 → 券后价超线红字
    _add(db, "PPS9301", "PPS9301011", "岩板餐桌-1.4米", 2600, "555", "800041",
         big_buyer=2000, mid_buyer=2046, floor=1800)
    db.commit()
    rep = pf.activity_preflight(db)
    assert rep["floor_conflict_count"] == 1
    fc = rep["floor_conflicts"][0]
    assert fc["sku_code"] == "PPS9301011" and fc["enrolled_floor"] == 1800.0 and fc["over"] > 0
    # 无订单 → 该商品近60天0销量 → 动销警示
    assert any(it["taobao_item_id"] == "555" for it in rep["no_sales_items"])
    # 占位SKU封顶后不超线
    _add(db, "PPS9302", "PPS9302011", "床-1.8米", 2860, "666", "800051", big_buyer=2200, mid_buyer=2250)
    _add(db, "PPS9302", "PPS9302099", "尺寸定制C", 1500, "666", "800052", ph=True, floor=400)
    db.commit()
    rep2 = pf.activity_preflight(db)
    assert all(c["sku_code"] != "PPS9302099" for c in rep2["floor_conflicts"])

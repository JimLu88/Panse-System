"""Task 9: 一键回填 — backfill_orders 用对应表给已导入订单补 店铺/产品编码/SKU编码。"""
from __future__ import annotations

from app.models.order import Order
from app.models.taobao_listing import TaobaoListing
from app.services import taobao_listing_service


def _listing(db, **kw):
    db.add(TaobaoListing(taobao_item_id=kw.get("item", "111"),
                         taobao_sku_id=kw.get("sku_id"),
                         sku_code=kw.get("sku_code"),
                         product_code=kw.get("product_code"),
                         shop=kw.get("shop"), matched=True))
    db.commit()


def test_backfill_fills_shop_and_product_code(db_session):
    _listing(db_session, sku_code="PPS2638004022511",
             product_code="PPS26380040225", shop="畔色店")
    db_session.add(Order(platform="淘宝", order_no="O1", sku_code="PPS2638004022511"))
    db_session.commit()
    res = taobao_listing_service.backfill_orders(db_session)
    assert res["updated"] == 1
    o = db_session.query(Order).filter_by(order_no="O1").one()
    assert o.shop == "畔色店"
    assert o.product_code == "PPS26380040225"


def test_backfill_skips_orders_that_already_have_shop(db_session):
    _listing(db_session, sku_code="PPS2638004022511",
             product_code="PPS26380040225", shop="畔色店")
    db_session.add(Order(platform="淘宝", order_no="O2",
                         sku_code="PPS2638004022511", shop="孚格店"))
    db_session.commit()
    res = taobao_listing_service.backfill_orders(db_session)  # 默认只扫 shop 为空
    assert res["scanned"] == 0
    o = db_session.query(Order).filter_by(order_no="O2").one()
    assert o.shop == "孚格店"   # 不覆盖已有店铺


def test_backfill_no_match_leaves_order_unchanged(db_session):
    db_session.add(Order(platform="淘宝", order_no="O3", sku_code="UNKNOWN"))
    db_session.commit()
    res = taobao_listing_service.backfill_orders(db_session)
    assert res["updated"] == 0
    o = db_session.query(Order).filter_by(order_no="O3").one()
    assert o.shop is None

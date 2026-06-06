"""Task 6: 订单导入按 skuId(精确) / 16位商家编码 经对应表反查 SKU编码/产品编码/店铺。"""
from __future__ import annotations

import csv
import io

from app.models.order import Order
from app.models.taobao_listing import TaobaoListing
from app.services import taobao_order_import


def _sales_detail_csv(rows: list[dict]) -> bytes:
    headers = ["主订单编号", "子订单编号", "商品属性", "商家编码", "skuId",
               "购买数量", "买家应付货款", "订单状态"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


def _seed_listing(db, **kw):
    db.add(TaobaoListing(taobao_item_id=kw.get("item", "111"),
                         taobao_sku_id=kw.get("sku_id"),
                         sku_code=kw.get("sku_code"),
                         product_code=kw.get("product_code"),
                         shop=kw.get("shop"), matched=True))
    db.commit()


def test_order_resolves_by_sku_id(db_session):
    _seed_listing(db_session, sku_id="SKU999", sku_code="PPS2638004022511",
                  product_code="PPS26380040225", shop="畔色店")
    data = _sales_detail_csv([{
        "主订单编号": "O1", "子订单编号": "O1-1",
        "商品属性": "颜色分类:榉木床头柜-标准;", "商家编码": "PPS2638004022511",
        "skuId": "SKU999", "购买数量": "1", "买家应付货款": "880", "订单状态": "交易成功",
    }])
    rep = taobao_order_import.import_taobao_orders(db_session, "销售明细.csv", data)
    assert rep.inserted == 1
    order = db_session.query(Order).one()
    assert order.sku_code == "PPS2638004022511"
    assert order.product_code == "PPS26380040225"   # 对应表覆盖了 P+11 兜底
    assert order.shop == "畔色店"                     # 分店归属


def test_order_resolves_by_merchant_code_when_no_sku_id(db_session):
    # 无 skuId, 但 16位商家编码命中对应表 -> 仍能拿到店铺
    _seed_listing(db_session, sku_id=None, sku_code="PPS2638004022511",
                  product_code="PPS26380040225", shop="孚格店")
    data = _sales_detail_csv([{
        "主订单编号": "O2", "子订单编号": "O2-1",
        "商品属性": "颜色分类:榉木床头柜-标准;", "商家编码": "PPS2638004022511",
        "skuId": "", "购买数量": "1", "买家应付货款": "880", "订单状态": "交易成功",
    }])
    rep = taobao_order_import.import_taobao_orders(db_session, "销售明细.csv", data)
    assert rep.inserted == 1
    order = db_session.query(Order).one()
    assert order.shop == "孚格店"


def test_order_no_listing_match_leaves_shop_none(db_session):
    # 对应表里没有 -> 不报错, shop 为空, sku_code 回退到商家编码
    data = _sales_detail_csv([{
        "主订单编号": "O3", "子订单编号": "O3-1",
        "商品属性": "颜色分类:某未知SKU;", "商家编码": "PPS9999999999911",
        "skuId": "NOPE", "购买数量": "1", "买家应付货款": "100", "订单状态": "交易成功",
    }])
    rep = taobao_order_import.import_taobao_orders(db_session, "销售明细.csv", data)
    assert rep.inserted == 1
    order = db_session.query(Order).one()
    assert order.shop is None
    assert order.sku_code == "PPS9999999999911"

# -*- coding: utf-8 -*-
"""淘宝标题 → 定价表回填 + 无编码订单按标题对回编码走定价表成本 (用户拍板 2026-06-18)。"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import order_sync_service
from app.services import taobao_title_import_service as tts


def _seed_pricing(db):
    # 理论成本口径(2026-06-18)=物理总成本(商品+物流+安装), 不含税/扣点; 会计成本(含税扣点)仅供对账
    db.add(PricingSku(product_code="PPS2398001060612", sku_code="PPS2398001060612",
                      sku="樱桃木-1.4米", physical_cost=Decimal("4500"),
                      accounting_cost=Decimal("5000"), factory_cost=Decimal("4200")))
    db.flush()


def test_import_titles_fills_pricing(db_session):
    db = db_session
    _seed_pricing(db)
    rows = [tts.TitleRow(product_code="PPS2398001060612",
                         sku_code="PPS2398001060612",
                         title="畔色实木餐边柜樱桃木一体")]
    res = tts.import_titles(db, rows)
    assert res.by_sku_code == 1
    ps = db.execute(select(PricingSku)).scalar_one()
    assert ps.taobao_title == "畔色实木餐边柜樱桃木一体"


def test_import_titles_marks_product_listed(db_session):
    # 在售导出=见到即在售 (2026-07-10): 命中产品 listing_status 置「在售」; 未见到的绝不反标下架
    from app.models.product import Product
    db = db_session
    _seed_pricing(db)
    db.add(Product(code="PPS2398001060612", name="测试餐边柜", listing_status="下架"))
    db.add(Product(code="PPS9999", name="别的产品", listing_status="下架"))
    db.flush()
    res = tts.import_titles(db, [tts.TitleRow(product_code="PPS2398001060612",
                                              sku_code="PPS2398001060612",
                                              title="畔色实木餐边柜樱桃木一体")])
    assert res.listed_marked == 1
    got = {p.code: p.listing_status for p in db.execute(select(Product)).scalars()}
    assert got["PPS2398001060612"] == "在售"
    assert got["PPS9999"] == "下架"        # 缺席≠下架, 不反标


def test_no_code_order_matched_by_title_uses_pricing_cost(db_session):
    db = db_session
    _seed_pricing(db)
    tts.import_titles(db, [tts.TitleRow(product_code="PPS2398001060612",
                                        sku_code="PPS2398001060612",
                                        title="畔色实木餐边柜樱桃木一体")])
    # 无编码订单, 名字 == 淘宝标题, 描述 == SKU 描述
    db.add(Order(platform="淘宝", order_no="N1", product_code=None, sku_code=None,
                 product_name="畔色实木餐边柜樱桃木一体", sku="樱桃木-1.4米",
                 qty=1, order_date=date(2026, 1, 4), status="paid",
                 paid_amount=Decimal("11000")))
    db.flush()

    n = order_sync_service.backfill_code_from_taobao_title(db)
    assert n == 1
    o = db.execute(select(Order).where(Order.order_no == "N1")).scalar_one()
    assert o.product_code == "PPS2398001060612"
    assert o.sku_code == "PPS2398001060612"
    # 成本来自定价表物理总成本 4500 (商品+物流+安装, 不含税/扣点), 不是 实付×百分比
    assert o.theoretical_cost is not None and float(o.theoretical_cost) == 4500.0


def test_small_payment_fragment_gets_85pct_cost(db_session):
    db = db_session
    _seed_pricing(db)  # physical_cost=4500
    tts.import_titles(db, [tts.TitleRow(product_code="PPS2398001060612",
                                        sku_code="PPS2398001060612",
                                        title="畔色实木餐边柜樱桃木一体")])
    # 实付¥200 远低于 4500×0.5 → 差价/定金片段: 挂上编码(保产品链接), 成本按 实付×85%=170 兜底
    db.add(Order(platform="淘宝", order_no="F1", product_code=None, sku_code=None,
                 product_name="畔色实木餐边柜樱桃木一体", sku="",
                 qty=1, order_date=date(2026, 1, 6), status="paid",
                 paid_amount=Decimal("200")))
    db.flush()
    order_sync_service.backfill_code_from_taobao_title(db)
    f = db.execute(select(Order).where(Order.order_no == "F1")).scalar_one()
    assert f.product_code == "PPS2398001060612"
    assert f.theoretical_cost is not None and float(f.theoretical_cost) == 170.0


def test_closed_unpaid_order_not_settled():
    """关闭/未付款单(实付0)即便状态是 signed 也不算成交 (用户拍板 2026-06-18)。"""
    from app.services.sales_analytics import is_settled_sale

    class _O:
        pass
    closed = _O()
    closed.status = "signed"; closed.paid_amount = Decimal("0")
    closed.refund_amount = Decimal("0"); closed.product_name = "畔色餐边柜"
    assert is_settled_sale(closed) is False
    real = _O()
    real.status = "signed"; real.paid_amount = Decimal("1644")
    real.refund_amount = Decimal("0"); real.product_name = "畔色餐边柜"
    assert is_settled_sale(real) is True


def test_zero_cost_link_order_not_matched(db_session):
    db = db_session
    _seed_pricing(db)
    tts.import_titles(db, [tts.TitleRow(product_code="PPS2398001060612",
                                        sku_code="PPS2398001060612",
                                        title="畔色实木餐边柜樱桃木一体")])
    # 差价/专链单 — 即便没编码也不该被挂产品编码 (zero_cost_reason 命中)
    db.add(Order(platform="淘宝", order_no="Z1", product_code=None, sku_code=None,
                 product_name="畔色木作 差价邮费补拍专链", sku="补差价",
                 qty=1, order_date=date(2026, 1, 5), status="paid",
                 paid_amount=Decimal("100")))
    db.flush()

    order_sync_service.backfill_code_from_taobao_title(db)
    z = db.execute(select(Order).where(Order.order_no == "Z1")).scalar_one()
    assert z.product_code is None

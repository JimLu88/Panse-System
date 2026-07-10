"""定价总表按淘宝ID搜索 (用户需求 2026-07-10).

覆盖两条搜索路径:
- /api/pricing-skus (精选视图, pricing.list_pricing_skus)
- /api/table-explorer/pricing_sku (全部列视图, table_explorer.get_table_data)
均需能按 taobao_item_id / taobao_sku_id / alt_taobao_sku_ids(一码多SKU) 命中。
"""
from decimal import Decimal

from app.api.pricing import list_pricing_skus
from app.api.table_explorer import get_table_data
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo


def _seed(db):
    db.add(PricingSku(product_code="PPS10010010101", sku_code="PPS1001001010111",
                      sku="测试柜-1.2米", product_name="测试柜", daily_price=Decimal("1000")))
    db.add(PricingSku(product_code="PPS10010010101", sku_code="PPS1001001010112",
                      sku="测试柜-1.5米", product_name="测试柜", daily_price=Decimal("1200")))
    db.add(PricingSkuPromo(sku_code="PPS1001001010111",
                           taobao_item_id="1046992019256", taobao_sku_id="6241068342539",
                           alt_taobao_sku_ids=["6260591734384"]))
    db.add(PricingSkuPromo(sku_code="PPS1001001010112",
                           taobao_item_id="1046992019256", taobao_sku_id="6241068342540"))
    db.commit()


def _list(db, q):
    return list_pricing_skus(q=q, size_category=None, category=None,
                             product_code=None, limit=50, offset=0, db=db, _=None)


def _explore(db, q):
    return get_table_data(entity="pricing_sku", q=q, limit=50, offset=0, db=db, _=None)


def test_search_by_taobao_item_id(db_session):
    _seed(db_session)
    out = _list(db_session, "1046992019256")
    assert out.total == 2                      # 商品ID 命中该商品全部 SKU
    codes = {i.sku_code for i in out.items}
    assert codes == {"PPS1001001010111", "PPS1001001010112"}


def test_search_by_taobao_sku_id(db_session):
    _seed(db_session)
    out = _list(db_session, "6241068342539")
    assert out.total == 1
    assert out.items[0].sku_code == "PPS1001001010111"


def test_search_by_alt_taobao_sku_id(db_session):
    _seed(db_session)
    out = _list(db_session, "6260591734384")   # 一码多SKU 的 alt 也要能搜
    assert out.total == 1
    assert out.items[0].sku_code == "PPS1001001010111"


def test_search_still_matches_codes(db_session):
    _seed(db_session)
    assert _list(db_session, "PPS1001001010112").total == 1   # 原有编码搜索不回退
    assert _list(db_session, "测试柜").total == 2


def test_table_explorer_search_by_taobao_ids(db_session):
    _seed(db_session)
    out = _explore(db_session, "1046992019256")
    assert out.total == 2                       # 全部列视图: 商品ID
    out = _explore(db_session, "6241068342540")
    assert out.total == 1                       # 全部列视图: SKUID
    out = _explore(db_session, "6260591734384")
    assert out.total == 1                       # 全部列视图: alt
    out = _explore(db_session, "不存在的东西")
    assert out.total == 0

from decimal import Decimal

from app.models.pricing import PricingSku
from app.models.taobao_listing import TaobaoListing
from app.services.order_completeness_incident import _sales_fact_matches_pricing


TITLE = '同一件淘宝商品'
PRODUCT = 'PPS24150030513'


def _price(db, code, option):
    price = PricingSku(product_code=PRODUCT, sku_code=code, sku=option,
                       taobao_title=TITLE, physical_cost=Decimal('100'))
    db.add(price)
    db.flush()
    return price


def _listing(db, sku_id, option, code=None, *, title=TITLE, product=PRODUCT):
    db.add(TaobaoListing(taobao_item_id='ITEM', taobao_sku_id=sku_id,
                         title=title, sku_spec=f'颜色分类:{option};',
                         sku_code=code, product_code=product, matched=True))
    db.flush()


def _fact(option, code=None, *, title=TITLE):
    return {'product_name': title, 'sku': option, 'sku_code': code}


def test_historical_option_can_use_exact_listing_alias(db_session):
    price = _price(db_session, 'PPS2415003051315', '组合柜-抽屉-双抽')
    _listing(db_session, 'SKU1', '双抽屉柜', price.sku_code)
    assert _sales_fact_matches_pricing(db_session, _fact('双抽屉柜'), price)
    assert not _sales_fact_matches_pricing(db_session, _fact('单抽屉柜'), price)


def test_missing_listing_code_needs_unique_exact_pricing_option(db_session):
    price = _price(db_session, 'PPS2415003051311', '榉木样块')
    _listing(db_session, 'SKU1', '榉木样块')
    assert _sales_fact_matches_pricing(db_session, _fact('榉木样块'), price)
    _price(db_session, 'PPS2415003051312', '榉木样块')
    assert not _sales_fact_matches_pricing(db_session, _fact('榉木样块'), price)


def test_conflicting_source_or_listing_sku_is_never_overridden(db_session):
    price = _price(db_session, 'PPS2415003051311', '开放柜')
    other = _price(db_session, 'PPS2415003051312', '隔板柜')
    _listing(db_session, 'SKU1', '开放柜', price.sku_code)
    assert not _sales_fact_matches_pricing(db_session, _fact('开放柜', other.sku_code), price)
    _listing(db_session, 'SKU2', '开放柜', other.sku_code)
    assert not _sales_fact_matches_pricing(db_session, _fact('开放柜'), price)


def test_wrong_product_or_title_never_qualifies(db_session):
    price = _price(db_session, 'PPS2415003051311', '开放柜')
    _listing(db_session, 'SKU1', '开放柜', price.sku_code, product='OTHER')
    assert not _sales_fact_matches_pricing(db_session, _fact('开放柜'), price)
    assert not _sales_fact_matches_pricing(db_session, _fact('开放柜', title='另一商品'), price)

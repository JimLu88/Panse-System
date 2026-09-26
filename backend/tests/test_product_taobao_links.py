from app.models.product import Product
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services.product_taobao_links import build_link_maps, numeric_id
import pytest


@pytest.mark.parametrize('raw', ['', None, '1e12', '12345.0', 'javascript:alert(1)', '0', '123&x=1'])
def test_bad_id_cannot_form_link(raw):
    assert numeric_id(raw) is None


def seed(db, *, item='123456789', tid='987654321', primary='111111111'):
    db.add(Product(code='P1', name='测试', taobao_id=primary, alt_taobao_ids=['222222222']))
    db.add(PricingSku(product_code='P1', sku_code='S1', sku='款一'))
    db.add(PricingSkuPromo(sku_code='S1', taobao_item_id=item, taobao_sku_id=tid))
    db.flush()


def test_current_sku_pair_not_stale_primary_or_alias(db_session):
    seed(db_session)
    products, skus = build_link_maps(db_session)
    assert skus['S1']['taobao_links'][0]['url'] == 'https://item.taobao.com/item.htm?id=123456789&skuId=987654321'
    assert products['P1']['taobao_links'][0]['item_id'] == '123456789'
    assert len(products['P1']['taobao_links']) == 1


def test_missing_sku_is_explicit_product_only(db_session):
    seed(db_session, tid=None)
    row=build_link_maps(db_session)[1]['S1']
    assert row['taobao_links'][0]['sku_id'] is None
    assert '缺SKU' in row['taobao_link_status']


def test_missing_item_never_combines_arbitrary_product_with_sku(db_session):
    seed(db_session, item=None)
    row=build_link_maps(db_session)[1]['S1']
    assert row['taobao_links'] == []
    assert '缺此SKU所属' in row['taobao_link_status']


def test_duplicate_taobao_sku_mapping_blocked_across_products(db_session):
    seed(db_session)
    db_session.add(PricingSku(product_code='P2', sku_code='S2'))
    db_session.add(PricingSkuPromo(sku_code='S2', taobao_item_id='333333333',taobao_sku_id='987654321'))
    db_session.flush()
    rows=build_link_maps(db_session)[1]
    assert not rows['S1']['taobao_links'] and not rows['S2']['taobao_links']


def test_multiple_item_mapping_does_not_guess_for_unmapped_style(db_session):
    seed(db_session)
    db_session.add_all([PricingSku(product_code='P1',sku_code='S2'),PricingSku(product_code='P1',sku_code='S3')])
    db_session.add(PricingSkuPromo(sku_code='S2',taobao_item_id='333333333'))
    db_session.flush()
    assert not build_link_maps(db_session)[1]['S3']['taobao_links']


def test_whitespace_id_is_normalized():
    assert numeric_id(' 123456 ') == '123456'


def test_placeholder_is_missing_not_invalid(db_session):
    seed(db_session, item='暂无', tid=None, primary=None)
    assert build_link_maps(db_session)[1]['S1']['taobao_link_status']=='缺淘宝商品映射'


def test_both_product_views_and_sku_api_expose_links(db_session, monkeypatch):
    from app.api import products, table_explorer
    from app.services import gallery_lookup
    seed(db_session)
    monkeypatch.setattr(gallery_lookup,'main_image_url_map',lambda *args:{})
    monkeypatch.setattr(gallery_lookup,'sku_gallery_url_map',lambda *args:{})
    rows=products.list_products(q=None,brand=None,category=None,sort=None,limit=500,offset=0,db=db_session)
    assert rows[0].taobao_links[0]['item_id']=='123456789'
    skus=products.list_product_skus('P1',db_session,None)
    assert skus[0].model_dump()['taobao_links'][0]['sku_id']=='987654321'
    for entity in ['product','pricing_sku']:
        data=table_explorer.get_table_data(entity,q=None,limit=50,offset=0,db=db_session,_=None)
        assert data.rows[0]['taobao_links']
        assert any(c.key=='taobao_links' for c in data.columns)

from datetime import date
import pytest
from app.models.product import Product
from app.models.pricing import PricingSku
from app.models.order import Order
from app.services import factory_sheet,order_sheet_archive_service as sheets


def setup(db,exact=None,single=False):
    db.add(Product(code='CAB',name='鞋柜',size_value='待定',size_detail='隔板上柜 900x220x1000mm'))
    db.add(PricingSku(product_code='CAB',sku_code='LOWER',sku='带抽翻门下柜',size_info=exact))
    if not single:db.add(PricingSku(product_code='CAB',sku_code='UPPER',sku='软木板上柜'))
    o=Order(platform='淘宝',order_no='P',order_date=date(2026,9,20),product_code='CAB',sku_code='LOWER',sku='带抽翻门下柜',qty=1)
    db.add(o);db.commit();return factory_sheet.build(db,o.id)


def test_lower_cabinet_never_inherits_upper_dimensions(db_session):
    sheet=setup(db_session)
    assert sheet.size_info is None
    assert any(w.code=='variant_size_unverified' for w in sheet.warnings)
    assert '隔板上柜 900' not in sheets.render_html(sheet)
    assert '本SKU：带抽翻门下柜' in sheets.render_html(sheet)


def test_unverified_size_stops_production_before_image_upload(db_session,monkeypatch):
    sheet=setup(db_session)
    monkeypatch.setattr(sheets,'_html_to_png',lambda *a,**k:pytest.fail('Must block before rendering/sending'))
    with pytest.raises(ValueError,match='专属尺寸'):sheets.render_png(sheet)


def test_exact_sku_dimensions_remain_primary(db_session):
    sheet=setup(db_session,'900x400x1100mm')
    assert sheet.size_info=='900x400x1100mm'
    assert not any(w.code=='variant_size_unverified' for w in sheet.warnings)


def test_single_variant_product_default_is_not_discarded(db_session):
    sheet=setup(db_session,single=True)
    assert sheet.size_info=='隔板上柜 900x220x1000mm'


def test_void_notice_still_works_without_dimension_basis(db_session,monkeypatch):
    sheet=setup(db_session)
    monkeypatch.setattr(sheets,'_html_to_png',lambda html,**kw:html)
    assert '作废' in sheets.render_void_png(sheet)


@pytest.mark.parametrize('quantity',[2,26,3200])
def test_custom_money_units_never_automatically_become_production_units(db_session,monkeypatch,quantity):
    setup(db_session,exact='900x400x1100mm')
    order=db_session.query(Order).filter_by(order_no='P').one()
    order.qty=quantity;order.sku='尺寸定制';order.is_custom=True;db_session.commit()
    sheet=factory_sheet.build(db_session,order.id)
    assert any(w.code=='production_quantity_unverified' for w in sheet.warnings)
    monkeypatch.setattr(sheets,'_html_to_png',lambda *a,**kw:pytest.fail('No ambiguous production units'))
    with pytest.raises(ValueError,match='实物数量'):sheets.render_png(sheet)


def test_lower_review_never_claims_production_ready(db_session,monkeypatch):
    from app.models.order import OrderDetail
    from app.services import order_completeness_incident as i
    setup(db_session)
    o=db_session.query(Order).filter_by(order_no='P').one();o.order_no=i.CABINET
    db_session.add(OrderDetail(order_no=i.CABINET,sub_order_no='5127637176073059926',sync_key='line:lower',
                    source='import',sku_code='PPS2455001090117',sku_name='带抽翻门下柜',qty=1))
    db_session.commit()
    built=factory_sheet.build(db_session,o.id)
    monkeypatch.setattr(factory_sheet,'build_for_order_line',lambda *args:built)
    html=i.lower_size_review_html(db_session)
    assert '不得用于生产或补发' in html and '专属尺寸待核对' in html
    assert '历史数量核对 · 尺寸待确认' in html
    assert built.image_url is None and built.sku_image is None

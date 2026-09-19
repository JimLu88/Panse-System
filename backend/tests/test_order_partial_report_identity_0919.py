from app.services import taobao_order_import as importer
from tests.test_order_line_factory_delivery_0812 import _order, _line


def test_shipping_blank_columns_cannot_erase_sales_variant_or_two_units(db_session):
    order = _order(db_session, 'MAIN-BEDSIDE')
    row = _line(db_session, order.order_no, order.order_no, '床头柜', 'PPS2438001051311')
    row.qty = 2
    db_session.commit()
    importer._persist_order_lines(db_session, order.order_no, [{
        'sub_order_no': order.order_no, 'product_name': None, 'sku_code': None,
        'qty': None, 'status_text': '买家已付款,等待卖家发货',
    }], importer.taobao_listing_service.build_resolver(db_session), enable_factory_delivery=True)
    db_session.flush()
    assert row.qty == 2 and row.sku_code == 'PPS2438001051311'
    assert row.sku_name == '床头柜' and row.factory_delivery_required


def test_master_with_sku_but_missing_quantity_keeps_two(db_session):
    order = _order(db_session, 'MAIN-TWO')
    row = _line(db_session, order.order_no, order.order_no, '床头柜', 'PPS2438001051311')
    row.qty = 2
    db_session.commit()
    importer._persist_order_lines(db_session, order.order_no, [{
        'sub_order_no': order.order_no, 'sku_code': row.sku_code,
        'status_text': '买家已付款,等待卖家发货',
    }], importer.taobao_listing_service.build_resolver(db_session))
    db_session.flush()
    assert row.qty == 2


def test_explicit_sales_quantity_still_updates(db_session):
    order = _order(db_session, 'MAIN-EXPLICIT')
    row = _line(db_session, order.order_no, order.order_no, '柜', 'PPS2438001051311')
    db_session.commit()
    importer._persist_order_lines(db_session, order.order_no, [{
        'sub_order_no': order.order_no, 'sku_code': row.sku_code, 'qty': 2,
        'status_text': '买家已付款,等待卖家发货',
    }], importer.taobao_listing_service.build_resolver(db_session))
    db_session.flush()
    assert row.qty == 2

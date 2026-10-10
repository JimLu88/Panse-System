from hashlib import sha256
from decimal import Decimal
import pytest
from app.services import taobao_order_import as imp
from app.services import order_line_delivery_service as delivery
from app.services import order_sheet_archive_service as sheets
from app.services import factory_sheet
from app.models.order import OrderDetail
from app.models.import_file import ImportedFile
from tests.test_order_line_factory_delivery_0812 import _order, _line


@pytest.mark.parametrize('title,service', [
    ('畔色实木藤编北欧入户玄关柜大容量储物柜超薄樱桃木换鞋凳鞋柜', False),
    ('免安装床头柜', False), ('实木餐桌包送货入户', False),
    ('商家安装 商家安装', True), ('送货入户', True),
    ('上门安装', True), ('安装服务', True), ('榉木床头柜', False),
])
def test_physical_product_is_not_a_service(title, service):
    assert imp._is_service_line_name(title) is service


def test_entry_cabinet_both_children_are_persisted(db_session):
    parent = '5127637176073013917'
    _order(db_session, parent)
    rows = [{'sub_order_no': sub, 'product_name': '畔色实木藤编北欧入户玄关柜',
             'sku_code': sku, 'sku': name, 'qty': 1}
            for sub, sku, name in [('5127637176073040745', 'PPS2455001090112', '软木板上柜'),
                                   ('5127637176073059926', 'PPS2455001090117', '带抽翻门下柜')]]
    for _ in range(2):
        imp._persist_order_lines(db_session, parent, rows, imp.taobao_listing_service.build_resolver(db_session))
        db_session.commit()
    assert db_session.query(OrderDetail).filter_by(order_no=parent, source='import').count() == 2


@pytest.mark.parametrize('quantity', [1, 2, 4, 11])
def test_quantity_visible_and_snapshot_is_rendered_value(db_session, monkeypatch, quantity):
    order = _order(db_session, 'P')
    row = _line(db_session, 'P', 'C', '柜', 'PPS2638004022511')
    row.qty = quantity; row.factory_no = 440
    sheet = factory_sheet.build_for_order_line(db_session, order.id, row.id)
    monkeypatch.setattr(sheets, '_gallery_data_uri', lambda *a: None)
    html = sheets.render_html(sheet)
    assert f'本子单共 {quantity} 件' in html
    captured = {}
    def archive(db, **kw):
        captured.update(kw)
        class Result: file = None
        return Result()
    monkeypatch.setattr(sheets.import_storage, 'archive', archive)
    row.qty = 99  # concurrent mutable state cannot falsify the sent snapshot
    sheets.archive_sent_line_snapshot(db_session, order, row, b'image', rendered_sheet=sheet)
    snap = captured['row_summary']['rendered_line']
    assert snap['qty'] == quantity
    assert snap['content_sha256'] == sha256(b'image').hexdigest()


@pytest.mark.parametrize('change,expected', [('qty','quantity_changed'),('sku_code','sku_changed'),
                                           ('product_code','product_changed'),('hash','image_hash_unverified')])
def test_changed_content_never_counts_as_verified(db_session, change, expected):
    _order(db_session, 'P')
    row = _line(db_session, 'P', 'C', '柜', 'PPS2638004022511')
    snap = {'schema':'factory-line-v2', 'qty':row.qty, 'sku_code':row.sku_code,
            'product_code':row.product_code, 'content_sha256':'abc'}
    evidence = ImportedFile(id=1, kind='order_sheet_sent', stored_path='unused', file_hash='abc',
                            row_summary={'rendered_line':snap})
    if change == 'hash': evidence.file_hash = 'other'
    else: setattr(row, change, 2 if change=='qty' else 'OTHER')
    result = delivery.audit_sent_line_content([row], {'C':evidence})
    assert expected in result['mismatches'][0]['reasons']


def test_historical_qty_not_backfilled_and_shipped_included(db_session):
    order = _order(db_session, 'P'); order.status = 'shipped'
    row = _line(db_session, 'P', 'C', '柜', 'PPS2638004022511');row.qty=2
    evidence = ImportedFile(id=1, kind='order_sheet_sent', stored_path='unused', row_summary={'pushed':True})
    result=delivery.audit_sent_line_content([row], {'C':evidence})
    assert result['unverified'][0]['reason']=='historical_render_snapshot_missing'
    assert 'rendered_line' not in evidence.row_summary


def test_refunded_line_not_a_resend_candidate(db_session):
    _order(db_session, 'P')
    row=_line(db_session,'P','C','柜','SKU',refunded=True)
    evidence=ImportedFile(id=1,kind='order_sheet_sent',stored_path='unused',row_summary={})
    assert delivery.audit_sent_line_content([row],{'C':evidence}) == {'mismatches':[], 'unverified':[]}


def test_matching_snapshot_verified(db_session):
    _order(db_session, 'P'); row=_line(db_session,'P','C','柜','SKU')
    evidence=ImportedFile(id=1,kind='order_sheet_sent',stored_path='unused',file_hash='abc',row_summary={
        'rendered_line':{'schema':'factory-line-v2','qty':1,'sku_code':'SKU','product_code':row.product_code,'content_sha256':'abc'}})
    assert delivery.audit_sent_line_content([row],{'C':evidence}) == {'mismatches':[], 'unverified':[]}


@pytest.mark.parametrize('name', ['尺寸定制', '差价补拍', '定制咨询'])
def test_money_unit_links_not_labeled_as_finished_pieces(db_session, monkeypatch, name):
    order=_order(db_session,'P');row=_line(db_session,'P','C',name,'SKU');row.qty=3200
    sheet=factory_sheet.build_for_order_line(db_session,order.id,row.id)
    sheet.sku=name
    monkeypatch.setattr(sheets,'_gallery_data_uri',lambda *a:None)
    html=sheets.render_html(sheet)
    assert '拍下数量 3200' in html and '本子单共 3200 件' not in html


@pytest.mark.parametrize('quantity,custom,expected', [(1,False,450),(2,False,900),(4,False,1800),(11,True,450)])
def test_wood_total_uses_same_quantity_as_theoretical(db_session,quantity,custom,expected):
    from tests.test_order_cost_pricing import _order as make_order, _pricing
    from app.services import order_cost_service as cost
    _pricing(db_session,physical_cost=Decimal('820'),wood_cost=Decimal('450'))
    order=make_order(db_session,qty=quantity);order.paid_amount=Decimal('15000');order.is_custom=custom
    order.actual_cost=Decimal('999')
    cost.recompute_and_save(db_session,order)
    assert order.wood_cost_est==Decimal(expected)
    assert order.actual_cost==Decimal('999')


def test_duplicate_child_cannot_overwrite_product(db_session):
    _order(db_session,'P')
    facts=[{'sub_order_no':'P','product_name':n,'sku_code':s,'qty':1} for n,s in [('桌','SKU1'),('柜','SKU2')]]
    with pytest.raises(ValueError,match='冲突商品'):
        imp._persist_order_lines(db_session,'P',facts,imp.taobao_listing_service.build_resolver(db_session))
    assert db_session.query(OrderDetail).count()==0


def test_identical_duplicate_child_is_one_line(db_session):
    _order(db_session,'P')
    fact={'sub_order_no':'C','product_name':'柜','sku_code':'SKU','qty':2}
    imp._persist_order_lines(db_session,'P',[fact,fact.copy()],imp.taobao_listing_service.build_resolver(db_session))
    db_session.commit()
    assert db_session.query(OrderDetail).count()==1


def test_cross_parent_child_cannot_be_stolen(db_session):
    _order(db_session,'P');_order(db_session,'OTHER');_line(db_session,'OTHER','C','柜','SKU')
    with pytest.raises(ValueError,match='其他主单'):
        imp._persist_order_lines(db_session,'P',[{'sub_order_no':'C','product_name':'柜','sku_code':'SKU','qty':2}],imp.taobao_listing_service.build_resolver(db_session))


def test_incident_sent_notification_never_replays(db_session):
    from app.services.order_completeness_incident import send_once
    calls=[]
    def send():calls.append(1);return {'message_id':'om_test'}
    assert send_once(db_session,'test','hash',send)['status']=='sent'
    assert send_once(db_session,'test','hash',send)['existing'] is True
    assert calls==[1]


def test_incident_unknown_send_never_retries(db_session):
    from app.services.order_completeness_incident import send_once
    calls=[]
    def send():calls.append(1);raise TimeoutError()
    assert send_once(db_session,'test','hash',send)['status']=='unknown'
    assert send_once(db_session,'test','hash',send)['status']=='unknown'
    assert calls==[1]


def test_incident_changed_notification_rejected(db_session):
    from app.services.order_completeness_incident import send_once
    send_once(db_session,'test','hash',lambda:{'message_id':'om_test'})
    with pytest.raises(ValueError,match='内容变化'):
        send_once(db_session,'test','new',lambda:pytest.fail('must not send'))


def test_incident_recovery_atomic_and_idempotent(db_session,monkeypatch):
    from types import SimpleNamespace
    from app.services import order_completeness_incident as incident
    from app.models.pricing import PricingSku
    raw=b'verified';digest=sha256(raw).hexdigest()
    monkeypatch.setattr(incident,'SOURCE_HASH',digest)
    monkeypatch.setattr(incident.import_storage,'read',lambda path:raw)
    db_session.add(ImportedFile(id=incident.SOURCE_ID,kind='taobao',stored_path='source',file_hash=digest,original_filename='source.xlsx'))
    parsed={}
    for parent,(number,fid) in incident.REVIEWED.items():
        order=_order(db_session,parent);order.status='shipped'
        order.actual_cost=Decimal('123');order.sku_code='PPS2638004022511'
        if parent==incident.CABINET:
            facts=[{'sub_order_no':s,'sku_code':sku,'product_name':'入户玄关柜','sku':name,'qty':'1','status_text':'卖家已发货'} for s,sku,name in
                   [('5127637176073040745','PPS2455001090112','软木板上柜'),('5127637176073059926','PPS2455001090117','带抽翻门下柜')]]
        else:
            row=_line(db_session,parent,parent,'床头柜','PPS2638004022511');row.qty=2
            facts=[{'sub_order_no':parent,'sku_code':row.sku_code,'product_name':'床头柜','sku':'标准','qty':'2'}]
        parsed[parent]=SimpleNamespace(lines=facts)
        db_session.add(ImportedFile(id=fid,kind='order_sheet_sent',stored_path='old',file_hash=digest,row_summary={'order_no':parent,'pushed':True}))
    for sku in ('PPS2638004022511','PPS2455001090112','PPS2455001090117'):
        db_session.add(PricingSku(product_code=sku[:-2],sku_code=sku,physical_cost=Decimal('820'),wood_cost=Decimal('450')))
    db_session.commit()
    monkeypatch.setattr(imp,'_parse_sales_detail',lambda *a:parsed)
    assert sum(x['missing'] for p in incident.prepare(db_session) for x in p['lines'])==2
    first=incident.repair(db_session)
    assert incident.repair(db_session)==first
    rows=db_session.query(OrderDetail).filter_by(order_no=incident.CABINET,source='import').all()
    assert len(rows)==2 and not any(x.factory_delivery_required for x in rows)
    from app.models.order import Order
    assert all(o.actual_cost==Decimal('123') for o in db_session.query(Order))
    assert first['new_production_orders']==0


def test_incident_notify_metadata_fits_production_schema(db_session,monkeypatch):
    from types import SimpleNamespace
    from app.models.settings import SystemSetting
    from app.services import order_completeness_incident as incident
    order=_order(db_session,'P');line=_line(db_session,'P','C','柜','SKU')
    db_session.add(SystemSetting(key=incident.INCIDENT+':repair',value_plain='{}',is_secret=False))
    db_session.commit()
    monkeypatch.setattr(incident,'prepare',lambda db:[{'order_no':'P','factory_no':350,'old_file_id':1,'lines':[{'sub_order_no':'C'}]}])
    monkeypatch.setattr(incident.settings_service,'get',lambda *a,**k:'oc_19d0a696aca01173f99d3276ec921f5b')
    monkeypatch.setattr(incident,'correction_html',lambda *a:'<body>test</body>')
    monkeypatch.setattr(incident.sheets,'_html_to_png',lambda *a,**k:b'image')
    monkeypatch.setattr(incident.feishu_client,'send_text',lambda *a:{'message_id':'om_notice'})
    monkeypatch.setattr(incident.feishu_client,'upload_image',lambda *a:'image_key')
    monkeypatch.setattr(incident.feishu_client,'send_image',lambda *a:{'message_id':'om_image'})
    def archive(db,**kw):
        # SQLite does not enforce VARCHAR(n), PostgreSQL does. Check the actual
        # model contract before the first production archive/send.
        assert len(kw['source'])<=ImportedFile.__table__.c.source.type.length
        assert kw['kind'] in incident.import_storage.KINDS
        return SimpleNamespace(file=SimpleNamespace(id=99))
    monkeypatch.setattr(incident.import_storage,'archive',archive)
    assert incident.notify(db_session)['status']=='sent'
    assert incident.notify(db_session)['status']=='sent'  # no repeated outbound

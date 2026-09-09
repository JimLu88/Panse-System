from datetime import date
from decimal import Decimal
from unittest.mock import Mock
import pytest
from sqlalchemy import event
from app.models.order import Order, OrderDetail
from app.services import factory_dispatch_feishu_service as d


def seed(db, statuses=('signed',), parent_status='paid', **kw):
    parent = Order(platform='淘宝', order_no='TEST-PARENT', order_date=date.today(),
                   status=parent_status, factory_no=1, paid_amount=1000,
                   sku_code='SKU', sku='实木', product_name='桌子', qty=1, **kw)
    db.add(parent)
    for i, status in enumerate(statuses):
        db.add(OrderDetail(order_no=parent.order_no, sub_order_no=f'TEST-LINE-{i}',
               source='import', factory_no=100+i, factory_delivery_required=True,
               line_status=status, sku_code=f'SKU-{i}', sku_name='实木',
               product_name='桌子', qty=1, amount=1000))
    db.commit()
    return parent


def mock_remote(monkeypatch, records, *, reject=False, lie=False):
    monkeypatch.setattr(d.feishu_client, 'list_table_fields', lambda *a: [
        {'field_name': n, 'type': t, 'is_primary': n == '工厂下单号'} for n,t in d.FIELD_SPECS])
    monkeypatch.setattr(d.feishu_client, 'list_views', lambda *a: [
        {'view_id': i, 'view_name': n, 'view_type': t} for i,(n,t) in d.EXPECTED_VIEWS.items()])
    monkeypatch.setattr(d.feishu_client, 'list_records', lambda *a: records)
    calls = []
    def create(db, app, table, rows):
        if reject:
            return [''] * len(rows)
        ids = []
        for row in rows:
            rid = 'new-' + str(len(records))
            ids.append(rid)
            records.append({'record_id': rid, 'fields': dict(row)})
        calls.append('create')
        return ids
    def update(db, app, table, rows):
        calls.append(rows)
        if reject:
            return [r['record_id'] for r in rows]
        if not lie:
            for row in rows:
                next(r for r in records if r['record_id'] == row['record_id'])['fields'].update(row['fields'])
        return []
    monkeypatch.setattr(d.feishu_client, 'batch_create_records', create)
    monkeypatch.setattr(d.feishu_client, 'batch_update_records', update)
    for name in ('batch_delete_records', 'create_field', 'update_field', 'delete_field'):
        monkeypatch.setattr(d.feishu_client, name, Mock(side_effect=AssertionError('forbidden mutation')))
    return calls


@pytest.mark.parametrize('status,expected', [('signed','已签收'),('shipped','已发货'),('paid','生产中')])
def test_child_status_beats_paid_parent(db_session, status, expected):
    seed(db_session, (status,))
    row = d.build_rows(db_session)[0]
    assert row['订单状态'] == expected
    if status != 'paid':
        assert row['交期紧急度'] == '完成'
        assert row['发货安排'] == expected
    else:
        assert row['发货安排'] == '做好直接发货'


def test_partial_shipment_does_not_promote_siblings(db_session):
    seed(db_session, ('signed','shipped','paid'), parent_status='signed', tracking_no='PARENT-TRACK')
    rows = d.build_rows(db_session)
    assert [r['订单状态'] for r in rows] == ['已签收','已发货','生产中']
    assert all(r['物流单号'] is None for r in rows)


def test_partial_refund_keeps_delivered_line(db_session):
    seed(db_session)
    line = db_session.query(OrderDetail).one()
    line.refund_amount, line.refund_status = Decimal('5'), '退款成功'
    db_session.commit()
    assert d.build_rows(db_session)[0]['订单状态'] == '已签收'
    line.refund_amount = Decimal('1000')
    db_session.commit()
    assert d.build_rows(db_session) == []


def test_refund_pending_does_not_claim_cancelled(db_session):
    seed(db_session, ('paid',))
    line = db_session.query(OrderDetail).one()
    line.refund_status = '退款中'
    db_session.commit()
    assert d.build_rows(db_session)[0]['订单状态'] == '售后中'


def test_all_filtered_import_lines_never_fall_back_to_parent(db_session):
    seed(db_session)
    line = db_session.query(OrderDetail).one()
    line.factory_delivery_required = False
    db_session.commit()
    assert d.build_rows(db_session) == []


def test_preview_never_writes_db_or_remote(db_session, monkeypatch):
    seed(db_session)
    records = []
    calls = mock_remote(monkeypatch, records)
    monkeypatch.setattr(d, '_attachment_value', Mock(side_effect=AssertionError('no upload')))
    monkeypatch.setattr(db_session, 'commit', Mock(side_effect=AssertionError('no commit')))
    monkeypatch.setattr(db_session, 'flush', Mock(side_effect=AssertionError('no flush')))
    result = d.preview_diff(db_session)
    assert result['ok'] and result['planned_creates'] == 1
    assert not calls and not db_session.dirty and not db_session.new


def test_sync_all_managed_fields_preserves_manual_unknown_and_images(db_session, monkeypatch):
    seed(db_session, customer_phone='123', buyer_message='发货前拍照')
    records = [{'record_id':'r1','fields':{'订单号':'TEST-PARENT','子订单号':'TEST-LINE-0',
        '工厂下单号':'畔色100单','订单状态':'生产中', '工厂手工备注':'不能动',
        '工厂下单图':[{'file_token':'manual-image'}], '木作成本价':123}},
        {'record_id':'unknown','fields':{'订单号':'UNKNOWN','工厂手工备注':'保留'}}]
    calls = mock_remote(monkeypatch, records)
    result = d.sync(db_session)
    assert result['ok'] and result['updated'] == 1
    assert records[0]['fields']['订单状态'] == '已签收'
    assert records[0]['fields']['工厂手工备注'] == '不能动'
    assert records[0]['fields']['工厂下单图'] == [{'file_token':'manual-image'}]
    assert records[0]['fields']['木作成本价'] == 123
    assert result['unmatched_preserved'] == 1
    second = d.sync(db_session)
    assert second['ok'] and second['updated'] == 0 and second['created'] == 0
    assert second['field_changes'] == {}


@pytest.mark.parametrize('reject,lie', [(True,False),(False,True)])
def test_write_failure_and_false_success_never_report_ok(db_session, monkeypatch, reject, lie):
    seed(db_session)
    records = [{'record_id':'r1','fields':{'订单号':'TEST-PARENT','子订单号':'TEST-LINE-0','订单状态':'生产中'}}]
    mock_remote(monkeypatch, records, reject=reject, lie=lie)
    result = d.sync(db_session, include_images=False)
    assert not result['ok'] and result['errors']


def test_duplicate_identity_fails_before_writes(db_session, monkeypatch):
    seed(db_session)
    records = [{'record_id':str(i),'fields':{'订单号':'TEST-PARENT','子订单号':'TEST-LINE-0'}} for i in (1,2)]
    calls = mock_remote(monkeypatch, records)
    result = d.sync(db_session)
    assert not result['ok'] and not calls


def test_concurrent_sync_rejected_without_calling_remote(db_session, monkeypatch):
    monkeypatch.setattr(d, '_sync_unlocked', Mock(side_effect=AssertionError('no second writer')))
    d._SYNC_LOCK.acquire()
    try:
        assert not d.sync(db_session)['ok']
    finally:
        d._SYNC_LOCK.release()


def test_explicit_note_clear_diff_but_unknown_cost_preserved():
    payload = d._business_payload({'订单备注':'', '客户延期单':False, '木作成本价':None, '未知人工列':'overwrite'})
    assert payload == {'订单备注':'', '客户延期单':False}
    changes = d._delta({'订单备注':'old', '客户延期单':True}, payload)
    assert changes['订单备注'] == '' and changes['客户延期单'] is False
    assert '系统更新时间' in changes


def test_no_table_target_expansion(db_session, monkeypatch):
    monkeypatch.setattr(d, '_target', lambda db: ('other','other'))
    assert not d.preview_diff(db_session)['ok']


def test_unknown_child_never_inherits_signed_parent(db_session):
    seed(db_session, (None,), parent_status='signed')
    row = d.build_rows(db_session)[0]
    assert row['订单状态'] == '待核实'
    assert row['发货安排'] == '待核实（暂勿发货）'


def test_exact_pricing_sku_fills_missing_spec_without_sibling_guess(db_session):
    from app.models.pricing import PricingSku
    seed(db_session)
    line = db_session.query(OrderDetail).one()
    line.sku_name = None
    db_session.add(PricingSku(sku_code='SKU-0', product_code='P0', sku='精确规格', wood_cost=321))
    db_session.commit()
    row = d.build_rows(db_session)[0]
    assert row['SKU规格'] == '精确规格' and row['产品编码'] == 'P0'
    assert row['木作成本价'] == 321


def test_permissions_failure_stops_before_write(db_session, monkeypatch):
    seed(db_session)
    calls = mock_remote(monkeypatch, [])
    monkeypatch.setattr(d.feishu_client, 'list_table_fields', Mock(side_effect=d.feishu_client.FeishuError('denied')))
    result = d.sync(db_session)
    assert not result['ok'] and not calls


def test_ambiguous_legacy_row_preserved_without_duplicate_create(db_session, monkeypatch):
    seed(db_session)
    records = [{'record_id':'legacy','fields':{'订单号':'TEST-PARENT', '工厂下单号':'畔色999单'}}]
    calls = mock_remote(monkeypatch, records)
    result = d.sync(db_session)
    assert result['identity_unresolved_count'] == 1 and not calls and result['created'] == 0

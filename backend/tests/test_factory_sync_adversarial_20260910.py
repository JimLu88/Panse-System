"""Promoted audit regressions. All DB and Feishu state is isolated.

Clear assertions retain the original expected result, now supplying the new
explicit provenance contract. Unknown blanks have separate preservation tests.
"""
import csv
import io
from datetime import date, datetime, timezone, timedelta
import pytest
from app.models.order import Order, OrderDetail
from app.services import feishu_client as fc, order_flags as flags
from app.services import factory_dispatch_feishu_service as fd, taobao_order_import as ti
from tests.test_factory_dispatch_field_contract import seed, mock_remote


def parse_notes(*, buyer='等通知发货', seller='开始制作', present=True, status='买家已付款，等待卖家发货'):
    output = io.StringIO()
    writer = csv.writer(output)
    header = ['子订单编号', '主订单编号', '商品标题', '购买数量', '订单创建时间', '订单状态']
    values = ['AUDIT-LINE', 'AUDIT-PARENT', '实木桌', 1, '2026-09-01', status]
    if present:
        header += ['主订单买家留言', '商家备注']
        values += [buyer, seller]
    writer.writerow(header)
    writer.writerow(values)
    return ti._parse_sales_detail('isolated-audit.csv', output.getvalue().encode('utf-8-sig'), ti.TaobaoImportReport())


def commit_notes(db, **kwargs):
    parsed = parse_notes(**kwargs)
    for row in parsed.values():
        row.source_kind = 'agent_download'
        row.source_observed_at = datetime.now(timezone.utc)
    ti._commit_orders(db, parsed, '淘宝', ti.TaobaoImportReport())
    return db.query(Order).filter_by(order_no='AUDIT-PARENT').one()


def test_pagination_normal_two_pages(monkeypatch):
    calls = []
    def request(db, method, url, **kwargs):
        calls.append(dict(kwargs['params']))
        if len(calls) == 1:
            return {'items': [{'record_id': 'r1'}], 'has_more': True, 'page_token': 'next'}
        return {'items': [{'record_id': 'r2'}], 'has_more': False}
    monkeypatch.setattr(fc, '_req', request)
    assert [r['record_id'] for r in fc.list_records(None, 'test-app', 'test-table')] == ['r1', 'r2']
    assert calls[1]['page_token'] == 'next'


def test_pagination_missing_token_must_not_succeed_with_partial_data(monkeypatch):
    monkeypatch.setattr(fc, '_req', lambda *a, **kw: {'items': [{'record_id': 'partial'}], 'has_more': True})
    with pytest.raises(fc.FeishuError):
        fc.list_records(None, 'test-app', 'test-table')


def test_pagination_repeated_token_must_stop_itself(monkeypatch):
    calls = []
    def request(*a, **kw):
        calls.append(dict(kw['params']))
        if len(calls) > 3:
            raise RuntimeError('AUDIT_STOP: repeated token still requested after three pages')
        return {'items': [{'record_id': 'r'+str(len(calls))}], 'has_more': True, 'page_token': 'loop'}
    monkeypatch.setattr(fc, '_req', request)
    with pytest.raises(fc.FeishuError):
        fc.list_records(None, 'test-app', 'test-table')
    assert len(calls) == 2


def test_pagination_second_page_error_propagates(monkeypatch):
    def request(*a, **kw):
        if kw['params'].get('page_token'):
            raise fc.FeishuError('isolated second page failure')
        return {'items': [{'record_id': 'r1'}], 'has_more': True, 'page_token': 'next'}
    monkeypatch.setattr(fc, '_req', request)
    with pytest.raises(fc.FeishuError):
        fc.list_records(None, 'test-app', 'test-table')


@pytest.mark.parametrize('payload', [
    {'items': []}, {'items': [], 'has_more': 'false'},
    {'items': {}, 'has_more': False}, {'items': [{}], 'has_more': False},
    {'items': [{'record_id':'dup'}, {'record_id':'dup'}], 'has_more':False},
])
def test_pagination_malformed_response_is_never_complete(monkeypatch,payload):
    monkeypatch.setattr(fc,'_req',lambda *a,**kw:payload)
    with pytest.raises(fc.FeishuError):fc.list_records(None,'test-app','test-table')


def test_pagination_unique_but_unbounded_pages_stop_at_limit(monkeypatch):
    count=0
    def request(*a,**kw):
        nonlocal count
        count+=1
        return {'items':[{'record_id':str(count)}],'has_more':True,'page_token':str(count)}
    monkeypatch.setattr(fc,'_req',request)
    with pytest.raises(fc.FeishuError,match='pagination_limit_exceeded'):
        fc.list_records(None,'test-app','test-table')
    assert count==1000


@pytest.mark.parametrize('field,param', [('buyer_message', 'buyer'), ('seller_memo', 'seller')])
def test_explicit_blank_platform_note_must_clear_old_note(db_session, field, param):
    order = commit_notes(db_session)
    assert getattr(order, field)
    commit_notes(db_session, **{param: ''})
    assert not getattr(order, field), 'Explicit source blank retained previous platform note'


def test_missing_note_columns_preserve_old_notes(db_session):
    order = commit_notes(db_session)
    commit_notes(db_session, present=False)
    assert order.buyer_message == '等通知发货' and order.seller_memo == '开始制作'


def test_nonempty_note_change_reaches_projected_notes(db_session):
    order = commit_notes(db_session)
    commit_notes(db_session, buyer='发货前拍照', seller='新备注')
    assert order.buyer_message == '发货前拍照' and order.seller_memo == '新备注'
    assert not flags.waits_for_shipping_notice(order)


@pytest.mark.parametrize('status,expected', [('shipped', '已发货'), ('signed', '已签收')])
def test_terminal_child_with_both_delayed_and_open_sibling(db_session, status, expected):
    seed(db_session, (status, 'paid'), parent_status=status, customer_shipping_month='2026-12',
         is_customer_delayed=True, customer_shipping_preserve_production=True, tracking_no='PARENT-ONLY')
    rows = fd.build_rows(db_session)
    assert rows[0]['订单状态'] == expected and rows[0]['发货安排'] == expected
    assert rows[0]['交期紧急度'] == '完成' and rows[0]['订单提醒'] == ''
    assert rows[1]['订单状态'] == '客户延期' and rows[1]['发货安排'] == '做好后等通知发货'
    assert all('物流单号' not in fd._business_payload(r) for r in rows)


def test_withdrawn_shipping_status_returns_to_production(db_session, monkeypatch):
    parent = seed(db_session, ('shipped',), parent_status='shipped', tracking_no='OLD-TRACK')
    records = [{'record_id': 'r1', 'fields': dict(fd.build_rows(db_session)[0])}]
    mock_remote(monkeypatch, records)
    parent.status = 'paid'
    db_session.query(OrderDetail).one().line_status = 'paid'
    db_session.commit()
    result = fd.sync(db_session, include_images=False)
    assert result['ok'] and records[0]['fields']['订单状态'] == '生产中'
    assert records[0]['fields']['发货安排'] == '做好直接发货'
    assert records[0]['fields']['物流单号'] == 'OLD-TRACK'  # Known retained history, not deletion authority.


def test_explicit_erp_tracking_clear_cannot_clear_remote_without_provenance(db_session, monkeypatch):
    from app.services import platform_field_provenance as provenance
    parent = seed(db_session, ('shipped',), tracking_no='OLD-TRACK')
    before = ti._OrderRow(order_no=parent.order_no, status_text='卖家已发货，等待买家确认',
        source_fields={'tracking_no': {'values': ['OLD-TRACK'], 'complete': True}},
        source_sha256='baseline', source_kind='agent_download',
        source_observed_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    provenance.apply(parent, before, [])
    records = [{'record_id': 'r1', 'fields': dict(fd.build_rows(db_session)[0])}]
    mock_remote(monkeypatch, records)
    after = ti._OrderRow(order_no=parent.order_no, status_text='买家已付款，等待卖家发货',
        source_fields={'tracking_no': {'values': [''], 'complete': True}},
        source_sha256='withdrawal', source_kind='agent_download', source_observed_at=datetime.now(timezone.utc))
    provenance.apply(parent, after, [])
    db_session.commit()
    result = fd.sync(db_session, include_images=False)
    assert result['ok']
    assert not records[0]['fields'].get('物流单号'), 'Explicit clear and unknown are conflated; old remote tracking retained'


@pytest.mark.parametrize('today,days,label', [(date(2026, 9, 9), 12, '正常安排'), (date(2026, 9, 10), 11, '紧急')])
def test_414_midnight_threshold_is_date_driven(today, days, label):
    order = Order(order_no='AUDIT-414', platform='淘宝', status='paid', order_date=date(2026, 8, 25),
                  ship_deadline=date(2026, 9, 21))
    schedule = flags.factory_schedule(order, today=today)
    assert schedule['effective_deadline'] == date(2026, 9, 21)
    assert schedule['days_left'] == days and schedule['urgency_label'] == label


@pytest.mark.parametrize('today', [date(2026, 12, 1), date(2026, 12, 31), date(2027, 1, 1)])
def test_confirmed_month_never_auto_releases_on_calendar_boundary(today):
    order = Order(order_no='AUDIT-MONTH', platform='淘宝', status='paid', factory_no=375,
                  customer_shipping_month='2026-12', is_customer_delayed=True,
                  customer_shipping_preserve_production=True, ship_deadline=date(2026, 8, 31))
    schedule = flags.factory_schedule(order, today=today)
    assert schedule['effective_deadline'] is None and schedule['days_left'] is None
    assert flags.waits_for_shipping_notice(order)


def test_old_active_factory_order_is_not_removed_by_date_window(db_session):
    order = seed(db_session, ('paid',))
    order.order_date = date(2020, 1, 1)
    db_session.commit()
    assert len(fd.build_rows(db_session)) == 1


def test_cancelled_parent_and_child_excluded_not_missing_active_order(db_session):
    seed(db_session, ('cancelled',), parent_status='cancelled')
    assert fd.build_rows(db_session) == []


def test_noop_preserves_manual_image_and_previous_fail_can_retry_once(db_session, monkeypatch):
    seed(db_session)
    records = [{'record_id': 'r1', 'fields': {'订单号': 'TEST-PARENT', '子订单号': 'TEST-LINE-0',
                '订单状态': '生产中', '人工备注': '不能改', '工厂下单图': [{'file_token': 'manual'}]}}]
    mock_remote(monkeypatch, records, lie=True)
    assert not fd.sync(db_session, include_images=False)['ok']
    mock_remote(monkeypatch, records)
    assert fd.sync(db_session, include_images=False)['ok']
    last = fd.sync(db_session, include_images=False)
    assert last['ok'] and last['updated'] == 0 and last['created'] == 0
    assert records[0]['fields']['人工备注'] == '不能改'
    assert records[0]['fields']['工厂下单图'] == [{'file_token': 'manual'}]

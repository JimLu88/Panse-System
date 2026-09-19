import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.settings import SystemSetting
from app.services import import_storage, settings_service
from app.services import order_scoped_resume_service as svc
from app.services import order_sheet_archive_service as sheets
from tests.test_order_line_factory_delivery_0812 import _order, _line, _feishu


def _csv(rows):
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


@pytest.fixture
def case(db_session, monkeypatch, tmp_path):
    monkeypatch.setenv('DELIVERY_STORAGE_ROOT', str(tmp_path))
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    parent, sub = '3316871605005127989', '3316871605005137171'
    order = _order(db_session, parent)
    row = _line(db_session, parent, sub, '柜', order.sku_code)
    row.factory_delivery_required = True
    data = {
        'orders': _csv([['订单编号', '订单状态', '买家实付金额', '商家编码'],
                        [parent, '买家已付款,等待卖家发货', 5000, order.sku_code]]),
        'sales_detail': _csv([['主订单编号', '子订单编号', '商品属性', '商家编码', '购买数量', '订单状态'],
                              [parent, sub, '柜', order.sku_code, 1, '买家已付款,等待卖家发货']]),
        'shipping': b'imported encrypted shipping evidence',
    }
    files = []
    for role, raw in data.items():
        r = import_storage.archive(db_session, content=raw, original_name=role+'.csv', kind='taobao',
                                   source='api', row_summary={'agent_status':'imported','agent_report_role':role}).file
        r.created_at = now
        files.append(r.id)
    settings_service.set_value(db_session, 'feishu_push_chat_id', 'factory-chat')
    db_session.commit()
    return row, dict(business_date=now.date().isoformat(),
                     order_batch_id=f'orders-{now:%Y%m%d}-'+'a'*32,
                     request_id='b'*32, file_ids=files, sub_order_nos=[sub])


def test_preview_no_write_or_send(db_session, case, _feishu):
    row, payload = case
    before = db_session.query(SystemSetting).count()
    result = svc.resume(db_session, **payload)
    assert result['ready'] == [row.sub_order_no]
    assert db_session.query(SystemSetting).count() == before
    assert row.factory_no is None and _feishu == []


def test_exact_scope_once_and_original_pipeline_untouched(db_session, case, _feishu):
    row, payload = case
    other = _line(db_session, row.order_no, '3316871605005146353', '桌', row.sku_code)
    other.factory_delivery_required = True
    settings_service.set_value(db_session, 'web_agent_ingest_state', '{"original":"unchanged"}')
    db_session.commit()
    result = svc.resume(db_session, **payload, dry_run=False)
    assert result['status'] == 'done' and len(result['receipts']) == 1
    assert result['receipts'][0]['message_id'] == 'img'
    assert other.factory_delivery_state is None
    assert settings_service.get(db_session, 'web_agent_ingest_state') == '{"original":"unchanged"}'
    assert svc.resume(db_session, **payload, dry_run=False)['existing_request'] is True
    assert len(_feishu) == 1


@pytest.mark.parametrize('state', ['sent', 'uncertain', 'sending_image', 'rendering'])
def test_never_repeat_sent_or_unknown(db_session, case, _feishu, state):
    row, payload = case
    row.factory_delivery_state = state
    db_session.commit()
    result = svc.resume(db_session, **payload, dry_run=False)
    assert result['status'] == 'partial' and _feishu == []


def test_file_tampering_rejected_before_claim(db_session, case, _feishu):
    from app.models.import_file import ImportedFile
    row, payload = case
    db_session.get(ImportedFile, payload['file_ids'][0]).file_hash = '0'*64
    db_session.commit()
    with pytest.raises(ValueError, match='文件校验'):
        svc.resume(db_session, **payload, dry_run=False)
    assert _feishu == []


def test_child_not_in_file_held(db_session, case, _feishu):
    row, payload = case
    payload['sub_order_nos'] = ['9999999999999999999']
    result = svc.resume(db_session, **payload, dry_run=False)
    assert result['held'][0]['reason'] == 'missing_imported_child'
    assert _feishu == []


def test_empty_allowlist_cannot_mean_global(db_session, case, _feishu):
    row, payload = case
    assert sheets.reconcile_order_line_delivery(db_session, only_sub_order_nos=set())['pushed'] == 0
    with pytest.raises(ValueError):
        svc.resume(db_session, **{**payload, 'sub_order_nos': []}, dry_run=False)
    assert _feishu == []


def test_missing_message_id_is_unknown_not_success(db_session, case, _feishu, monkeypatch):
    row, payload = case
    monkeypatch.setattr('app.services.feishu_client.send_image', lambda *a: {})
    result = svc.resume(db_session, **payload, dry_run=False)
    assert result['status'] == 'partial' and result['receipts'] == []
    assert row.factory_delivery_state == 'uncertain'


def test_scope_cannot_change_under_same_request(db_session, case, _feishu):
    row, payload = case
    svc.resume(db_session, **payload, dry_run=False)
    with pytest.raises(ValueError, match='更换范围'):
        svc.resume(db_session, **{**payload, 'sub_order_nos':['9999999999999999999']}, dry_run=False)
    assert len(_feishu) == 1


def test_placeholder_restores_source_sku_and_two_units(db_session, case, _feishu):
    row, payload = case
    sku = row.sku_code
    row.sub_order_no = row.order_no
    row.sku_code = row.sku_name = row.product_code = None
    payload['sub_order_nos'] = [row.sub_order_no]
    raw = _csv([['主订单编号','子订单编号','商品属性','商家编码','购买数量','订单状态'],
                [row.order_no,row.sub_order_no,'床头柜',sku,2,'买家已付款,等待卖家发货']])
    rec = import_storage.archive(db_session, content=raw, original_name='sales.csv',kind='taobao',source='api',
                                 row_summary={'agent_status':'imported','agent_report_role':'sales_detail'}).file
    rec.created_at = datetime.now(ZoneInfo('Asia/Shanghai'))
    payload['file_ids'][1] = rec.id
    db_session.commit()
    result = svc.resume(db_session, **payload, dry_run=False)
    assert result['status'] == 'done' and row.qty == 2 and row.sku_code == sku
    assert result['repairs'][0]['before']['qty'] == 1
    assert len(_feishu) == 1


def test_normal_existing_quantity_conflict_not_overwritten(db_session, case, _feishu):
    row, payload = case
    row.qty = 3
    db_session.commit()
    result = svc.resume(db_session, **payload, dry_run=False)
    assert result['status'] == 'partial' and row.qty == 3 and _feishu == []


@pytest.mark.parametrize('field,value', [('agent_status','pending_password'),('agent_report_role','orders')])
def test_unresolved_or_duplicate_report_role_blocked(db_session, case, _feishu, field, value):
    from app.models.import_file import ImportedFile
    row, payload = case
    rec = db_session.get(ImportedFile,payload['file_ids'][2])
    rec.row_summary = {**rec.row_summary,field:value}
    db_session.commit()
    with pytest.raises(ValueError):
        svc.resume(db_session, **payload, dry_run=False)
    assert _feishu == []

import csv, io, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from app.models.order import Order, OrderDetail
from app.services import platform_field_provenance as p, taobao_order_import as ti
from app.services import factory_dispatch_feishu_service as fd
from tests.test_factory_dispatch_field_contract import seed, mock_remote

def snapshot(value, *, field='buyer_message', offset=-60, version=None, status='买家已付款，等待卖家发货'):
    return ti._OrderRow(order_no='TEST-PARENT', status_text=status, source_kind='agent_download',
        source_sha256=p.digest(version or value), source_observed_at=datetime.now(timezone.utc)+timedelta(seconds=offset),
        source_fields={field: {'values': [value], 'complete': True}})

@pytest.mark.parametrize('reason', ['missing_time','stale','future','manual_value','explicit_human','partial','conflict','older_version','unowned'])
def test_destructive_clear_requires_all_evidence(db_session, reason):
    order=seed(db_session, ('paid',), buyer_message='旧留言')
    if reason!='unowned': p.apply(order,snapshot('旧留言',offset=-120),[])
    incoming=snapshot('',offset=-60)
    if reason=='missing_time': incoming.source_observed_at=None
    if reason=='stale': incoming.source_observed_at=datetime.now(timezone.utc)-timedelta(days=3)
    if reason=='future': incoming.source_observed_at=datetime.now(timezone.utc)+timedelta(hours=1)
    if reason=='manual_value': order.buyer_message='人工修正'
    if reason=='explicit_human': incoming.protected_fields={'buyer_message'}
    if reason=='partial': incoming.source_scope_complete=False
    if reason=='conflict': incoming.source_fields['buyer_message']['values']=['','不同值']
    if reason=='older_version': incoming.source_observed_at=datetime.now(timezone.utc)-timedelta(minutes=10)
    before=order.buyer_message
    p.apply(order,incoming,[])
    assert order.buyer_message==before

def test_age_limit_does_not_freeze_nonempty_update(db_session):
    order=seed(db_session,('paid',),buyer_message='旧值')
    first=snapshot('旧值');first.source_observed_at=datetime.now(timezone.utc)-timedelta(days=5)
    p.apply(order,first,[])
    second=snapshot('正常更新');second.source_observed_at=datetime.now(timezone.utc)-timedelta(days=4)
    p.apply(order,second,[])
    assert order.buyer_message=='正常更新'

def test_unversioned_nonempty_update_does_not_inherit_clear_authority(db_session):
    order=seed(db_session,('paid',),buyer_message='旧值')
    p.apply(order,snapshot('旧值',offset=-120),[])
    changed=snapshot('正常非空更新');changed.source_observed_at=None
    p.apply(order,changed,[])
    assert order.buyer_message=='正常非空更新'
    p.apply(order,snapshot('',offset=-30),[])
    assert order.buyer_message=='正常非空更新'

def test_unknown_tracking_blank_preserves_remote(db_session,monkeypatch):
    order=seed(db_session,('shipped',),tracking_no='TRACK')
    records=[{'record_id':'r','fields':dict(fd.build_rows(db_session)[0])}]
    mock_remote(monkeypatch,records)
    order.tracking_no='';db_session.commit()
    assert fd.sync(db_session,include_images=False)['ok']
    assert records[0]['fields']['物流单号']=='TRACK'

def test_remote_manual_tracking_blocks_explicit_clear(db_session,monkeypatch):
    order=seed(db_session,('shipped',),tracking_no='TRACK')
    p.apply(order,snapshot('TRACK',field='tracking_no',status='卖家已发货，等待买家确认',offset=-120),[])
    records=[{'record_id':'r','fields':dict(fd.build_rows(db_session)[0],物流单号='MANUAL')}]
    mock_remote(monkeypatch,records)
    p.apply(order,snapshot('',field='tracking_no',offset=-60),[]);db_session.commit()
    result=fd.sync(db_session,include_images=False)
    assert not result['ok'] and result['protected_clear_conflicts']==1
    assert records[0]['fields']['物流单号']=='MANUAL'

def csv_snapshot(key,tracking,status,buyer='初始',seller='初始'):
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['主订单编号','子订单编号','商品标题','购买数量','订单创建时间','订单状态','主订单买家留言','商家备注','物流单号'])
    writer.writerow([key,key+'-0','实木桌',1,'2026-09-01',status,buyer,seller,tracking])
    return output.getvalue().encode('utf-8-sig')

def test_real_ingest_boundary_to_projection_to_remote_clear(db_session,monkeypatch,tmp_path):
    from app.services import agent_ingest_service as ingest
    monkeypatch.setattr(ingest,'OUTPUT_DIR',tmp_path)
    now=datetime.now(timezone.utc).timestamp()
    raw=csv_snapshot('PIPE','TRACK','卖家已发货，等待买家确认')
    path=tmp_path/'first.csv';path.write_bytes(raw);os.utime(path,(now-120,now-120))
    assert ingest._import_one(db_session,'taobao_report',path,raw)[1]=='imported'
    order=db_session.query(Order).filter_by(order_no='PIPE').one()
    order.factory_no=100;db_session.query(OrderDetail).filter_by(order_no='PIPE').one().factory_no=100
    db_session.commit()
    assert order.platform_field_state['tracking_no']['observed_at']==datetime.fromtimestamp(now-120,timezone.utc).isoformat()
    records=[];mock_remote(monkeypatch,records)
    assert fd.sync(db_session,include_images=False)['ok']
    raw2=csv_snapshot('PIPE','','买家已付款，等待卖家发货',buyer='',seller='')
    path2=tmp_path/'second.csv';path2.write_bytes(raw2);os.utime(path2,(now-60,now-60))
    assert ingest._import_one(db_session,'taobao_report',path2,raw2)[1]=='imported'
    assert order.buyer_message=='' and order.seller_memo=='' and order.tracking_no==''
    assert p.tracking_clear(order)
    assert fd.sync(db_session,include_images=False)['ok']
    assert records[0]['fields']['物流单号']==''
    assert fd.sync(db_session,include_images=False)['updated']==0
    # Old captured file cannot reintroduce cleared platform notes/tracking.
    ingest._import_one(db_session,'taobao_report',path,raw)
    assert order.buyer_message=='' and order.tracking_no==''

def test_partial_child_report_cannot_clear_whole_order_notes(db_session):
    full=csv_snapshot('PART','','买家已付款，等待卖家发货')
    parsed=ti._parse_sales_detail('test.csv',full,ti.TaobaoImportReport())
    row=parsed['PART'];row.source_kind='agent_download';row.source_observed_at=datetime.now(timezone.utc)-timedelta(minutes=2)
    row.lines.append({**row.lines[0],'sub_order_no':'PART-1'})
    ti._commit_orders(db_session,parsed,'淘宝',ti.TaobaoImportReport())
    clear=ti._parse_sales_detail('test.csv',csv_snapshot('PART','','买家已付款，等待卖家发货',buyer='',seller=''),ti.TaobaoImportReport())
    clear['PART'].source_kind='agent_download';clear['PART'].source_observed_at=datetime.now(timezone.utc)-timedelta(minutes=1)
    ti._commit_orders(db_session,clear,'淘宝',ti.TaobaoImportReport())
    assert db_session.query(Order).filter_by(order_no='PART').one().buyer_message=='初始'

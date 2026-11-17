import hashlib
import io
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.services import taobao_order_import as imp
from app.services import agent_ingest_service as ingest
from app.services import shipping_recovery_service as target
from app.services import feishu_bot_service as bot


def workbook(headers=None, values=None):
    wb = Workbook(); ws = wb.active; ws.title = 'export'
    ws.append(headers or ['订单编号', '订单状态', '商品标题', '商品属性SKU', '收货人姓名', '收货地址', '联系手机'])
    ws.append(values or ['3316569963241107867', '买家已付款', '餐桌', '颜色分类:砂白色2米', '测试', '测试市测试路123号', '13800000000'])
    out = io.BytesIO(); wb.save(out); return out.getvalue()


def test_plain_shipping_creates_no_default_child(db_session):
    db = db_session; raw = workbook()
    assert imp.detect_report_role('x.xlsx', raw) == 'shipping'
    rep = imp.import_taobao_orders(db, 'x.xlsx', raw)
    assert rep.inserted == 1 and not rep.errors
    o = db.scalar(select(Order))
    assert o.qty == 0 and o.sku == '砂白色2米' and o.platform_field_state['shipping_only']
    assert not list(db.scalars(select(OrderDetail)))
    from app.services import factory_sheet
    sheet = factory_sheet.build(db, o.id)
    assert any(w.code == 'sales_detail_missing' and w.severity == 'error' for w in sheet.warnings)


def test_shipping_preserves_real_child_quantity_finance_and_sent(db_session):
    db = db_session
    o = Order(order_no='3316569963241107867', platform='淘宝', status='paid', qty=2, paid_amount=500,
              customer_address='已有完整地址')
    ln = OrderDetail(order_no=o.order_no, sub_order_no='3317195424167030363', source='import', qty=2,
                     sku_name='原规格', factory_delivery_state='sent', factory_delivery_message_id='sent841')
    db.add_all([o, ln]); db.commit()
    imp.import_taobao_orders(db, 'x.xlsx', workbook())
    assert o.qty == 2 and o.paid_amount == 500 and ln.qty == 2 and ln.sku_name == '原规格'
    assert ln.factory_delivery_message_id == 'sent841' and len(list(db.scalars(select(OrderDetail)))) == 1


def test_null_address_is_not_fabricated(db_session):
    raw = workbook(values=['3316569963241107867','买家已付款','柜','洞石','测试','测试市null测试路123号','13800000000'])
    rep = imp.import_taobao_orders(db_session, 'x.xlsx', raw)
    assert db_session.scalar(select(Order)).customer_address is None
    assert any('null' in w for w in rep.warnings)


@pytest.mark.parametrize('qty', [None, '', '未知', 0, -1])
def test_sales_missing_quantity_does_not_release_shipping_gate(db_session, qty):
    db = db_session; imp.import_taobao_orders(db, 'x.xlsx', workbook())
    raw = workbook(['主订单编号','子订单编号','订单状态','商品标题','商品属性','购买数量','商家编码'],
                   ['3316569963241107867','3316569963241107868','买家已付款','餐桌','砂白色',qty,'PPS1234567890123'])
    imp.import_taobao_orders(db, 'sales.xlsx', raw)
    assert db.scalar(select(Order)).platform_field_state['shipping_only']


def test_no_target_never_tries_history_or_reports_wrong_password(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(ingest, 'latest_order_pull_evidence', lambda db: {})
    (tmp_path/'old.xlsx').write_bytes(imp._OOXML_ENCRYPTED_MAGIC + b'old')
    monkeypatch.setattr(imp, 'import_taobao_orders', lambda *a, **k: pytest.fail('history imported'))
    r = bot.apply_shipping_password(db_session, 'synthetic-secret')
    assert r['status'] == 'target_missing' and r['tried'] == 0
    saved = json.loads(db_session.scalar(select(SystemSetting).where(SystemSetting.key == ingest.KEY_SHIPPING_PASSWORD_RESULT)).value_plain)
    assert saved['status'] == 'target_missing' and not saved['pending_files']


@pytest.fixture
def registration(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, 'OUTPUT_DIR', tmp_path)
    from app.services import import_storage
    monkeypatch.setattr(import_storage, 'get_root', lambda: tmp_path/'archive')
    raw = workbook(); p = tmp_path/'ExportOrderList123.xlsx'; p.write_bytes(raw)
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    manifest = {'path': p.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'export_id':'123',
                'business_date':now.date().isoformat(),'generated_at':now.isoformat(),
                'order_batch_id':'orders-'+now.strftime('%Y%m%d')+'-'+'a'*32,'mode':'original'}
    return db_session, manifest, p


def test_registration_claim_and_password_are_exact_and_idempotent(registration, monkeypatch):
    db, manifest, p = registration
    assert target.register(db, manifest)['password_request_allowed']
    assert not target.register(db, manifest)['password_request_allowed']
    r = target.apply_password(db, 'test')
    assert r['imported'] == 1 and r['files'][0]['file'] == p.name
    monkeypatch.setattr(imp, 'import_taobao_orders', lambda *a, **kw: pytest.fail('reimport'))
    assert target.apply_password(db, 'test')['duplicate']
    assert len(list(db.scalars(select(Order)))) == 1


@pytest.mark.parametrize('mutation', ['hash','path','id','date','batch'])
def test_registration_rejects_wrong_identity(registration, mutation):
    db, m, p = registration
    if mutation=='hash': m['sha256']='0'*64
    if mutation=='path': m['path']='../'+p.name
    if mutation=='id': m['export_id']='124'
    if mutation=='date': m['generated_at']='2020-01-01T00:00:00+08:00'
    if mutation=='batch': m['order_batch_id']='wrong'
    with pytest.raises(ValueError): target.register(db,m)
    assert target._row(db, target.ACTIVE) is None


def test_changed_file_after_registration_is_not_imported(registration):
    db,m,p=registration;target.register(db,m);p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash'): target.apply_password(db,'test')
    assert not list(db.scalars(select(Order)))


def test_password_failure_does_not_claim_import_or_mix_other_files(registration,monkeypatch):
    db,m,p=registration;target.register(db,m)
    def wrong(*args): raise ValueError('synthetic mismatch')
    monkeypatch.setattr(imp,'maybe_decrypt',wrong)
    result=target.apply_password(db,'wrong')
    assert result['status']=='password_mismatch' and result['pending_files']==[p.name]
    assert not list(db.scalars(select(Order)))


def test_unresolved_target_cannot_be_replaced(registration):
    db,m,p=registration;target.register(db,m)
    other=p.with_name('ExportOrderList124.xlsx');other.write_bytes(workbook(values=['3316569963241107899','买家已付款','桌','砂白','测试','地址','13800000000']))
    with pytest.raises(ValueError,match='unresolved'):
        target.register(db,{**m,'path':other.name,'export_id':'124','sha256':hashlib.sha256(other.read_bytes()).hexdigest()})


@pytest.mark.parametrize('status', ['target_missing','needs_review','import_failed','processing'])
def test_non_password_failures_never_ask_for_another_password(db_session,monkeypatch,status):
    monkeypatch.setattr(bot,'apply_shipping_password',lambda *a:{'status':status,'failure_reason':'test processing issue','imported':0})
    monkeypatch.setattr(bot,'_relay_shipping_password',lambda *a:None)
    cards=[]
    monkeypatch.setattr(bot,'_safe_reply',lambda db,mid,card:cards.append(card))
    monkeypatch.setattr(bot,'_result_card',lambda title,body,color:{'title':title,'body':body})
    bot._capture_shipping_password(db_session,'mid','test')
    assert cards[0]['title']=='发货报表处理待核对'
    assert '正确口令' not in cards[0]['body'] and '口令未匹配' not in cards[0]['body']


def test_independent_registration_cross_midnight_sources_no_reimport_or_attempt_rewrite(registration, monkeypatch):
    from app.services import import_storage, order_scoped_resume_service as resume
    from app.models.import_file import ImportedFile
    db,m,p=registration
    today=datetime.now(ZoneInfo('Asia/Shanghai'))
    day=today.date()-timedelta(days=1)
    m.update(business_date=day.isoformat(),order_batch_id='orders-'+day.strftime('%Y%m%d')+'-'+'a'*32,
             mode='independent',sub_order_nos=['3316569963241107868'])
    rows={
        'orders':workbook(['订单编号','订单状态','买家实付金额'],['3316569963241107867','买家已付款',500]),
        'sales_detail':workbook(['主订单编号','子订单编号','商品属性','购买数量','商家编码'],
            ['3316569963241107867','3316569963241107868','砂白色',2,'PPS1234567890123'])}
    ids=[]
    for role,raw in rows.items():
        rec=import_storage.archive(db,content=raw,original_name=role+'.xlsx',kind='taobao',
            row_summary={'agent_status':'imported','agent_report_role':role,'automation_batch_id':m['order_batch_id']}).file
        rec.created_at=today;ids.append(rec.id)
    m['source_file_ids']=ids;db.commit()
    key=target.register(db,m)['target_id']
    shipping=import_storage.archive(db,content=p.read_bytes(),original_name=p.name,kind='taobao',
        row_summary={'agent_status':'imported','agent_report_role':'shipping'}).file
    shipping.created_at=today;db.commit()
    masters,details,evidence=resume._read_evidence(db,m['business_date'],ids+[shipping.id],key)
    assert masters and details and all(x['business_date']==day.isoformat() for x in evidence)
    calls=[]
    monkeypatch.setattr(resume,'resume',lambda db,**kw:(calls.append(kw) or {'status':'partial','held':[{'reason':'address_masked'}]}))
    monkeypatch.setattr(imp,'import_taobao_orders',lambda *a,**kw:pytest.fail('3963 reimported'))
    result=target.apply_password(db,'test')
    assert result['independent'] and result['imported']==1 and calls[0]['sub_order_nos']==m['sub_order_nos']
    assert calls[0]['shipping_target_id']==key and calls[0]['business_date']==day.isoformat()
    assert db.get(ImportedFile,shipping.id).row_summary.get('automation_batch_id') is None
    target.apply_password(db,'test')
    assert len(calls)==1


@pytest.mark.parametrize('token,expected',[('test-web',200),('wrong',401),('',401),('test-cs',403)])
def test_registration_http_machine_scope(monkeypatch,token,expected):
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from app import dependencies as deps
    from app.api.web_agent import router
    from app.database import get_db
    from types import SimpleNamespace
    monkeypatch.setenv('PANSE_AUTH_ENFORCE','1')
    keys={'web_agent_token':'test-web','cs_api_key':'test-cs'}
    monkeypatch.setattr(deps.settings_service,'get',lambda db,name,**kw:keys.get(name))
    calls=[]
    monkeypatch.setattr(target,'register',lambda db,body:(calls.append(body) or {'ok':True}))
    app=FastAPI(dependencies=[Depends(deps.require_authenticated),Depends(deps.enforce_page_permission)])
    app.include_router(router);app.dependency_overrides[get_db]=lambda:SimpleNamespace()
    with TestClient(app) as client:
        response=client.post('/api/web-agent/shipping-target',json={'test':True},headers={'X-API-Key':token})
    assert response.status_code==expected
    assert bool(calls)==(expected==200)


@pytest.mark.parametrize('sent_conflict',[False,True])
def test_bounded_existing_placeholder_repair_preserves_sent_and_never_imports(db_session,monkeypatch,tmp_path,sent_conflict):
    from app.cli import shipping_repair_20261117 as repair
    from app.models.import_file import ImportedFile
    from app.services import import_storage
    from types import SimpleNamespace
    db=db_session;raw=b'fixture';path=tmp_path/'original';path.write_bytes(raw)
    monkeypatch.setattr(import_storage,'get_root',lambda:tmp_path)
    monkeypatch.setattr(repair,'SOURCE_HASH',hashlib.sha256(raw).hexdigest())
    db.add(ImportedFile(id=3963,kind='taobao',file_hash=repair.SOURCE_HASH,original_filename='x.xlsx',
                       stored_path=str(path),size_bytes=len(raw),source='api',row_summary={'agent_status':'imported'}))
    parsed={}
    for no in repair.ORDERS:
        db.add(Order(order_no=no,platform='淘宝',status='paid',qty=1))
        db.add(OrderDetail(order_no=no,sub_order_no=no,source='import',qty=1,factory_delivery_required=True))
        parsed[no]=SimpleNamespace(lines=[{'qty':None,'explicit_child':False,'sku':'source SKU'}])
    for num,mid in repair.PROTECTED.items():
        db.add(OrderDetail(order_no='protected'+str(num),sub_order_no=str(num),source='import',qty=2,
                           factory_no=num,factory_delivery_state='sent',factory_delivery_message_id=mid))
    db.commit()
    monkeypatch.setattr(imp,'maybe_decrypt',lambda raw,pwd:raw)
    monkeypatch.setattr(imp,'_parse_sales_detail',lambda *a:parsed)
    monkeypatch.setattr(imp,'import_taobao_orders',lambda *a,**kw:pytest.fail('reimport'))
    if sent_conflict:
        db.scalar(select(OrderDetail).where(OrderDetail.order_no==repair.ORDERS[0])).factory_delivery_message_id='already-sent'
        db.commit()
        with pytest.raises(ValueError,match='no_overwrite'):repair.repair(db,apply=True)
        assert all(o.qty==1 for o in db.scalars(select(Order)))
        return
    assert not repair.repair(db)['applied']
    result=repair.repair(db,apply=True)
    assert result['applied'] and not result['imported'] and not result['sent'] and not result['deleted']
    for change in result['changes']:
        assert change['after']['source']=='shipping_only'
        assert len(change['after']['source'])<=OrderDetail.__table__.c.source.type.length
    assert all(o.qty==0 and o.platform_field_state['shipping_only'] for o in db.scalars(select(Order)))
    assert len(list(db.scalars(select(OrderDetail))))==7
    assert repair.repair(db,apply=True)['existing']
    for num,mid in repair.PROTECTED.items():
        assert db.scalar(select(OrderDetail).where(OrderDetail.factory_no==num)).factory_delivery_message_id==mid

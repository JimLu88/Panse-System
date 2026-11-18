"""No platform calls: exercise batch integrity, state gates and durable claims."""
import hashlib
import io
from datetime import date
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import select, func
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.order import Order, OrderDetail
from app.models.fulfillment_intake import FulfillmentBatch, FulfillmentIntake
from app.services import fulfillment_batch_service as service
from app.services.taobao_shipping import ShippingGate
from app.api.shipments import batch_file, ShippingPreview

TID = '99999999999999912345'
TRACK = '0000123456789'


def seed(db, tid=TID, shop='test', count=2):
    order = Order(platform='淘宝', order_no=tid, shop=shop, status='paid', qty=count,
                  order_date=date(2026, 1, 1))
    lines = [OrderDetail(source='import', order_no=tid, sub_order_no=tid + str(i),
                       qty=1, line_status='paid', product_name='测试家具', sku_name=f'规格{i}')
             for i in range(count)]
    db.add_all([order, *lines]); db.commit()
    return order, lines


def register(db, tid=TID, tracking=TRACK, **overrides):
    snapshot = service.preview(db, tid, '中通快运', tracking)
    args = dict(request_id=str(uuid4()), actor='ERP:1', order_no=tid, carrier='中通快运',
                tracking_no=tracking, snapshot_sha256=snapshot['snapshot_sha256'],
                all_goods_handed_over=True, notes_fulfilled=True)
    args.update(overrides)
    return service.register_manual(db, **args)


def test_workbook_preserves_template_and_text():
    data = service.workbook([dict(order_no=TID, carrier='中通快运', tracking_no=TRACK)])
    with ZipFile(io.BytesIO(data)) as result, ZipFile(service.TEMPLATE) as original:
        assert result.namelist() == original.namelist()
        for name in result.namelist():
            if name != 'xl/worksheets/sheet1.xml':
                assert result.read(name) == original.read(name)
        ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        sheet = ET.fromstring(result.read('xl/worksheets/sheet1.xml'))
        cells = sheet.findall('x:sheetData/x:row[@r="2"]/x:c', ns)
        assert [c.find('x:is/x:t', ns).text for c in cells] == [TID, '中通快运', TRACK]
        assert [c.attrib['s'] for c in cells] == ['3', '4', '5']
        assert all(c.attrib['t'] == 'inlineStr' for c in cells)
        assert sheet.find('x:dimension', ns).attrib['ref'] == 'A1:C2'


@pytest.mark.parametrize('field,value', [('order_no', 1234567890123456), ('order_no', '=1234567890'),
    ('carrier', '=HYPERLINK("bad")'), ('carrier', '中通'), ('carrier', '商家配送'),
    ('tracking_no', '12'), ('tracking_no', '=12345678')])
def test_bad_fields(field, value):
    row = dict(order_no=TID, carrier='中通快运', tracking_no=TRACK); row[field] = value
    with pytest.raises(ShippingGate): service.workbook([row])


@pytest.mark.parametrize('change', ['order', 'parcel'])
def test_duplicate_workbook(change):
    first = dict(order_no=TID, carrier='中通快运', tracking_no=TRACK)
    second = {**first, 'tracking_no': TRACK + '1'} if change == 'order' else {**first, 'order_no': TID + '1'}
    with pytest.raises(ShippingGate): service.workbook([first, second])


def test_altered_template_refused(monkeypatch, tmp_path):
    file = tmp_path / 'bad.xlsx'; file.write_bytes(b'changed')
    monkeypatch.setattr(service, 'TEMPLATE', file)
    with pytest.raises(ShippingGate, match='模板'): service.workbook([dict(order_no=TID, carrier='中通快运', tracking_no=TRACK)])


@pytest.mark.parametrize('target,field,value', [('order', 'status', 'shipped'), ('order', 'tracking_no', 'existing'),
    ('order', 'refund_status', '退款中'), ('order', 'remark', '等通知发货'), ('order', 'shop', ''),
    ('line', 'line_status', 'shipped'), ('line', 'qty', 0), ('line', 'sub_order_no', ''),
    ('line', 'remark', '等通知发货，开始制作'), ('line', 'remark', '暂不发货'),
    ('line', 'remark', '12月31日再发货')])
def test_unsafe_orders_blocked(db_session, target, field, value):
    order, lines = seed(db_session)
    setattr(order if target == 'order' else lines[0], field, value); db_session.commit()
    with pytest.raises(ShippingGate): service.preview(db_session, TID, '中通快运', TRACK)


def test_full_order_confirmation_notes_and_stale_snapshot(db_session):
    db = db_session; order, lines = seed(db)
    order.remark = '发出前必须岩板拍照'; db.commit()
    p = service.preview(db, TID, '中通快运', TRACK)
    assert len(p['lines']) == 2 and p['has_notes'] and p['notes'] == order.remark
    with pytest.raises(ShippingGate): register(db, all_goods_handed_over=False)
    with pytest.raises(ShippingGate): register(db, notes_fulfilled=False)
    lines[0].qty = 2; db.commit()
    with pytest.raises(ShippingGate, match='更新'): register(db, snapshot_sha256=p['snapshot_sha256'])
    row = register(db)
    assert row.state == 'ready' and row.sub_order_no is None
    assert order.status == 'paid' and order.tracking_no is None


def test_batch_idempotency_claims_and_original_download(db_session):
    db = db_session; seed(db)
    row = register(db); bid = str(uuid4())
    args = dict(request_id=bid, actor='ERP:1', message_ids=[row.message_id])
    batch = service.prepare(db, **args)
    assert service.prepare(db, **args).file_sha256 == batch.file_sha256
    assert db.scalar(select(func.count()).select_from(FulfillmentBatch)) == 1
    assert row.state == 'batch_prepared' and row.order_claim == TID
    from uuid import UUID
    assert batch_file(UUID(bid), db, None).body == batch.file_bytes
    assert batch.file_sha256 == hashlib.sha256(batch.file_bytes).hexdigest()
    with pytest.raises(ShippingGate): service.prepare(db, **{**args, 'request_id': str(uuid4())})
    duplicate = register(db, tracking=TRACK + '1')
    with pytest.raises(ShippingGate): service.prepare(db, request_id=str(uuid4()), actor='ERP:1', message_ids=[duplicate.message_id])
    db.rollback()
    assert db.scalar(select(func.count()).select_from(FulfillmentBatch)) == 1
    assert db.get(Order, db.scalar(select(Order.id))).status == 'paid'
    batch.file_bytes = b'corrupt'; db.commit()
    with pytest.raises(HTTPException) as error: batch_file(UUID(bid), db, None)
    assert error.value.status_code == 409


def test_changed_order_and_cross_shop_refused(db_session):
    db = db_session; order, lines = seed(db)
    first = register(db)
    lines[0].remark = '请拍照'; db.commit()
    with pytest.raises(ShippingGate, match='变化'):
        service.prepare(db, request_id=str(uuid4()), actor='ERP:1', message_ids=[first.message_id])
    first = register(db)
    seed(db, TID + '1', 'second-shop')
    second = register(db, TID + '1', TRACK + '1')
    with pytest.raises(ShippingGate, match='不同店铺'):
        service.prepare(db, request_id=str(uuid4()), actor='ERP:1', message_ids=[first.message_id, second.message_id])
    assert not db.scalar(select(FulfillmentBatch))


def test_manual_idempotency_and_input_validation(db_session):
    db = db_session; seed(db); rid = str(uuid4())
    first = register(db, request_id=rid)
    assert register(db, request_id=rid).message_id == first.message_id
    with pytest.raises(ShippingGate): register(db, request_id=rid, actor='ERP:2')
    with pytest.raises(ValidationError): ShippingPreview(order_no=1234567890123456, carrier='中通快运', tracking_no=TRACK)


def test_new_routes_require_shipping_operator():
    from app.api.shipments import router
    import inspect
    routes = [r for r in router.routes if 'batch' in r.path or 'manual' in r.path]
    assert len(routes) == 10
    for route in routes:
        gates = [d.call for d in route.dependant.dependencies if d.call is not None]
        assert any(set(inspect.getclosurevars(g).nonlocals.get('roles', ())) == {'admin', 'operator'} for g in gates)


def test_migration_preserves_existing_intake():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, text, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('batch_migration', Path(__file__).parents[1] / 'alembic/versions/0159_fulfillment_batches.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('create table fulfillment_intakes (message_id varchar(128) primary key, state varchar(32))'))
        conn.execute(text("insert into fulfillment_intakes values ('existing', 'unknown')"))
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        assert 'fulfillment_batches' in inspect(conn).get_table_names()
        assert conn.execute(text('select state, batch_id from fulfillment_intakes')).one() == ('unknown', None)
        module.downgrade()
        assert conn.execute(text('select state from fulfillment_intakes')).scalar() == 'unknown'
    engine.dispose()


def test_precheck_batch(db_session):
    db = db_session
    order, lines = seed(db)
    row = register(db)
    bid = str(uuid4())
    batch = service.prepare(db, request_id=bid, actor='ERP:1', message_ids=[row.message_id])

    # Precheck passes
    pre = service.precheck_batch(db, bid)
    assert pre['ok'] is True
    assert pre['count'] == 1
    assert pre['shop'] == 'test'

    # Precheck catches refund
    order.refund_status = '退款中'
    db.commit()
    with pytest.raises(ShippingGate, match='状态变化'):
        service.precheck_batch(db, bid)


def execution_fixture(db):
    order, lines = seed(db)
    row = register(db)
    batch = service.prepare(db, request_id=str(uuid4()), actor='ERP:1', message_ids=[row.message_id])
    attempt = str(uuid4())
    service.record_execution_start(db, batch_id=batch.id, actor='ERP:1', attempt_id=attempt)
    args = dict(batch_id=batch.id, actor='ERP:1', attempt_id=attempt,
                platform_task_id='987654321', file_sha256=batch.file_sha256)
    service.record_platform_submission(db, **args)
    return order, row, batch, args


def test_batch_execution_lifecycle_and_verified_receipt(db_session):
    from datetime import datetime, timezone
    db = db_session
    order, row, batch, args = execution_fixture(db)
    task = {'taskId': args['platform_task_id'], 'taskStatus': 'FINISHED', 'successCount': 1, 'failCount': 0}
    proof = {**batch.rows[0], 'source': 'taobao_logistics_readback', 'evidence_ref': 'synthetic-proof',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'seller_confirm': True,
        'trade_status': 'WAIT_BUYER_CONFIRM_GOODS'}
    finished = service.finish_batch_execution(db, **args, task_status='FINISHED', success_count=1,
        fail_count=0, order_results={TID: {'status': 'confirmed'}},
        raw_receipt={'task': task, 'shipping_readbacks': [proof]})
    assert finished.state == 'confirmed' and row.state == 'confirmed'
    # The imported official order report remains the order writer; no date guesses.
    assert order.status == 'paid' and order.tracking_no is None and order.ship_date is None
    with pytest.raises(ShippingGate):
        service.record_execution_start(db, batch_id=batch.id, actor='ERP:1', attempt_id=str(uuid4()))
    service.finish_batch_execution(db, **args, task_status='FINISHED', success_count=1,
        fail_count=0, order_results={TID: {'status':'unknown'}}, raw_receipt={'task':task})
    assert row.state == 'confirmed'


def test_failure_keeps_claims_and_cannot_restart(db_session):
    db = db_session
    order, row, batch, args = execution_fixture(db)
    task = {'taskId':args['platform_task_id'], 'taskStatus':'ERROR', 'successCount':0, 'failCount':1}
    result = service.finish_batch_execution(db, **args, task_status='ERROR', success_count=0,
        fail_count=1, order_results={TID:{'status':'failed'}}, raw_receipt={'task':task,
            'errors':{'success':True, 'data':[{'orderNo':TID,'errorMessage':'买家已申请退款'}]}})
    assert result.state == 'failed' and row.order_claim == TID and row.parcel_claim
    assert order.status == 'paid'
    with pytest.raises(ShippingGate):
        service.record_execution_start(db, batch_id=batch.id, actor='ERP:1', attempt_id=str(uuid4()))


@pytest.mark.parametrize('mutate', ['unproven_success','wrong_tracking','wrong_shop','wrong_child','missing_proof'])
def test_finish_rejects_unproved_status(db_session, mutate):
    from datetime import datetime, timezone
    db=db_session; order, row, batch, args=execution_fixture(db)
    task={'taskId':args['platform_task_id'],'taskStatus':'FINISHED','successCount':1,'failCount':0}
    proof={**batch.rows[0], 'source':'taobao_logistics_readback','evidence_ref':'test-proof',
           'observed_at':datetime.now(timezone.utc).isoformat(),'seller_confirm':True,'trade_status':'WAIT_BUYER_CONFIRM_GOODS'}
    if mutate=='wrong_tracking': proof['tracking_no']='DIFFERENT'
    if mutate=='wrong_shop': proof['shop']='other'
    if mutate=='wrong_child': proof['sub_order_nos']=['different']
    proofs=[] if mutate in ('unproven_success','missing_proof') else [proof]
    with pytest.raises(ShippingGate, match='官方物流证据'):
        service.finish_batch_execution(db, **args, task_status='FINISHED', success_count=1,fail_count=0,
            order_results={TID:{'status':'confirmed'}},raw_receipt={'task':task,'shipping_readbacks':proofs})
    assert row.state=='submitting' and order.status=='paid' and row.order_claim==TID


def test_start_once_and_task_binding(db_session):
    db=db_session; order,row,batch,args=execution_fixture(db)
    for attempt in (args['attempt_id'],str(uuid4())):
        with pytest.raises(ShippingGate):
            service.record_execution_start(db,batch_id=batch.id,actor='ERP:1',attempt_id=attempt)
    for field,value in [('platform_task_id','OTHER'),('attempt_id',str(uuid4())),('file_sha256','0'*64),('actor','ERP:2')]:
        with pytest.raises(ShippingGate): service.record_platform_submission(db,**{**args,field:value})
    assert service.record_platform_submission(db,**args).platform_task_id==args['platform_task_id']


def test_precheck_rejects_changed_snapshot(db_session):
    db=db_session; order,lines=seed(db); row=register(db)
    batch=service.prepare(db,request_id=str(uuid4()),actor='ERP:1',message_ids=[row.message_id])
    lines[0].remark='发货前必须拍照'; db.commit()
    with pytest.raises(ShippingGate): service.precheck_batch(db,batch.id)
    with pytest.raises(ShippingGate): service.record_execution_start(db,batch_id=batch.id,actor='ERP:1',attempt_id=str(uuid4()))
    assert batch.state=='prepared'


def test_platform_task_cannot_be_shared_by_two_batches(db_session):
    db=db_session; _,_,first,args=execution_fixture(db)
    other_tid=TID+'1'
    seed(db,other_tid); row=register(db,other_tid,TRACK+'1')
    second=service.prepare(db,request_id=str(uuid4()),actor='ERP:1',message_ids=[row.message_id])
    attempt=str(uuid4())
    service.record_execution_start(db,batch_id=second.id,actor='ERP:1',attempt_id=attempt)
    with pytest.raises(ShippingGate,match='绑定其他批次'):
        service.record_platform_submission(db,batch_id=second.id,actor='ERP:1',attempt_id=attempt,
            platform_task_id=args['platform_task_id'],file_sha256=second.file_sha256)
    assert first.platform_task_id==args['platform_task_id']
    assert second.platform_task_id is None and second.state=='submitting'


def test_execution_api_defaults_disabled(monkeypatch):
    from app.api.shipments import start_execution, StartExecution
    monkeypatch.delenv('PANSE_BATCH_SHIPPING_ENABLED',raising=False)
    with pytest.raises(HTTPException) as error:
        start_execution(uuid4(),StartExecution(),None,None)
    assert error.value.status_code==409


def test_migration_0160_execution():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, text, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location('mig_0160', Path(__file__).parents[1] / 'alembic/versions/0160_fulfillment_batch_execution.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('create table fulfillment_batches (id varchar(64) primary key, state varchar(32))'))
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        cols = {c['name'] for c in inspect(conn).get_columns('fulfillment_batches')}
        assert {'platform_task_id', 'execution'} <= cols
        module.downgrade()
        cols_down = {c['name'] for c in inspect(conn).get_columns('fulfillment_batches')}
        assert 'platform_task_id' not in cols_down
    engine.dispose()

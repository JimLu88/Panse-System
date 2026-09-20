import json
from datetime import date
from decimal import Decimal
from unittest.mock import Mock
import pytest
from app.models.order import Order, OrderDetail
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import factory_dispatch_feishu_service as d, factory_production_evidence as facts
from app.services import order_delivery_completion_service as c


def seed(db,qty=2,custom=True):
    db.add(Product(code='TABLE',name='餐桌'))
    db.add(PricingSku(sku_code='S',product_code='TABLE',sku='尺寸定制' if custom else '普通款',size_info='长1500mm'))
    o=Order(platform='淘宝',order_no='P',order_date=date.today(),status='paid',paid_amount=1000,
            qty=qty,is_custom=custom,actual_cost=Decimal('123.45'))
    line=OrderDetail(order_no='P',sub_order_no='C',source='import',qty=qty,sku_code='S',product_code='TABLE',
                     sku_name='尺寸定制' if custom else '普通款',factory_no=513,factory_delivery_required=True,factory_delivery_state='failed')
    db.add_all([o,line]);db.commit();return o,line


@pytest.mark.parametrize('qty',[2,4,11,None,0,-1])
def test_unknown_custom_or_invalid_quantity_is_never_one(db_session,qty):
    o,line=seed(db_session,qty)
    # SQLAlchemy defaults None on insert; explicitly represent legacy missing qty.
    line.qty=qty;db_session.flush()
    row=d.build_rows(db_session)[0]
    assert row['确认成品数量'] is None and row['订购数量'] is None
    assert row['下单分组']=='事实待核实' and '请勿生产' in row['数量确认状态']
    assert o.actual_cost==Decimal('123.45') and line.qty==qty
    payload=d._business_payload(row)
    assert payload['确认成品数量'] is None and payload['订购数量'] is None


@pytest.mark.parametrize('quantity',[1,2])
def test_confirmed_quantity_and_sheet_use_same_facts(db_session,quantity):
    o,line=seed(db_session)
    facts.confirm_quantity(db_session,line_id=line.id,expected_sku='S',expected_purchase_qty=2,
                           physical_qty=quantity,actor='user',evidence_ref='reply:example')
    row=d.build_rows(db_session)[0]
    assert row['订购数量']==row['确认成品数量']==quantity
    assert row['原购买数量']==line.qty==o.qty==2
    assert row['数量确认依据']=='reply:example' and row['数量确认状态']=='人工已确认'
    assert o.actual_cost==Decimal('123.45')


def test_sent_receipt_not_destroyed_when_confirmation_becomes_stale(db_session):
    o,line=seed(db_session)
    facts.confirm_quantity(db_session,line_id=line.id,expected_sku='S',expected_purchase_qty=2,
                           physical_qty=1,actor='user',evidence_ref='reply:example')
    line.factory_delivery_state='sent';line.factory_delivery_message_id='image-receipt'
    o.remark='最新尺寸变化';db_session.commit()
    row=d.build_rows(db_session)[0]
    assert row['确认成品数量'] is None and '待核实' in row['生产事实状态']
    assert line.factory_delivery_state=='sent' and line.factory_delivery_message_id=='image-receipt'


def test_regular_multi_quantity_is_not_collapsed(db_session):
    o,line=seed(db_session,4,False)
    assert d.build_rows(db_session)[0]['确认成品数量']==4


def test_exact_dimension_asset_matches_sheet_not_product_default(db_session,monkeypatch):
    o,line=seed(db_session,1,False)
    ps=db_session.query(PricingSku).one();ps.size_info=None
    db_session.add(PricingSku(sku_code='OTHER',product_code='TABLE'))
    db_session.commit()
    monkeypatch.setattr(facts,'verified_dimensions',lambda p,s:'720×380×360mm' if (p,s)==('TABLE','S') else None)
    row=d.build_rows(db_session)[0]
    assert row['尺寸']=='720×380×360mm'
    line.sku_code='OTHER';db_session.commit()
    row=d.build_rows(db_session)[0]
    assert row['下单分组']=='事实待核实' and '720' not in row['尺寸']


def test_partial_held_child_does_not_hide_sibling(db_session):
    o,line=seed(db_session)
    db_session.add(OrderDetail(order_no='P',sub_order_no='C2',source='import',qty=1,
        sku_code='S',product_code='TABLE',sku_name='普通款',factory_no=514,factory_delivery_required=True))
    db_session.commit()
    rows=d.build_rows(db_session)
    assert len(rows)==2 and rows[0]['确认成品数量'] is None and rows[1]['确认成品数量']==1


def test_closeout_projects_even_when_image_held_without_clearing_error(db_session,monkeypatch):
    from app.services import order_sheet_archive_service as sheets, automation_failure_recorder_service as failures
    monkeypatch.setattr(sheets,'reconcile_pending_delivery',lambda *a,**kw:{'_run_status':'fail','_error':'quantity_pending'})
    called=[]
    monkeypatch.setattr(d,'sync_if_enabled',lambda db:called.append(1) or {'ok':True})
    monkeypatch.setattr(failures,'record_callback_run',lambda *a,**kw:{'status':kw['status']})
    result=c.complete_recovered_order_delivery(db_session,source='test',manifest=[],order_business_date='2020-01-01')
    assert called==[1] and result['_run_status']=='fail' and 'quantity_pending' in result['_error']
    assert result['factory_dispatch']['ok']


def test_disabled_projection_does_not_claim_synchronized(db_session,monkeypatch):
    from app.services import order_sheet_archive_service as sheets, automation_failure_recorder_service as failures
    monkeypatch.setattr(sheets,'reconcile_pending_delivery',lambda *a,**kw:{})
    monkeypatch.setattr(d,'sync_if_enabled',lambda db:{'ok':True,'skipped':'auto_disabled'})
    captured=[]
    monkeypatch.setattr(failures,'record_callback_run',lambda *a,**kw:captured.append(kw) or {})
    c.complete_recovered_order_delivery(db_session,source='test',manifest=[],order_business_date='2020-01-01')
    assert '未同步' in captured[0]['detail'] and '已同步' not in captured[0]['detail']


@pytest.mark.parametrize('dry_run',[True,False])
def test_only_five_additive_fact_fields_can_be_created(db_session,monkeypatch,dry_run):
    fields=[{'field_name':n,'type':t,'is_primary':n=='工厂下单号'} for n,t in d.FIELD_SPECS if n not in d.PRODUCTION_FACT_FIELDS]
    calls=[]
    monkeypatch.setattr(d.feishu_client,'list_table_fields',lambda *a:fields)
    def create(db,app,table,name,kind):
        calls.append(name);fields.append({'field_name':name,'type':kind})
    monkeypatch.setattr(d.feishu_client,'create_field',create)
    monkeypatch.setattr(d.feishu_client,'list_views',lambda *a:[{'view_id':i,'view_type':t} for i,(n,t) in d.EXPECTED_VIEWS.items()])
    monkeypatch.setattr(d.feishu_client,'list_records',lambda *a:[])
    for name in ('delete_field','update_field','batch_delete_records'):
        monkeypatch.setattr(d.feishu_client,name,Mock(side_effect=AssertionError('not authorized')))
    result=d._sync_unlocked(db_session,dry_run=dry_run)
    assert set(calls)==(set() if dry_run else d.PRODUCTION_FACT_FIELDS)
    assert result['ok'] is (not dry_run)


def test_additive_schema_refuses_conflicting_existing_type(db_session,monkeypatch):
    fields=[{'field_name':n,'type':t,'is_primary':n=='工厂下单号'} for n,t in d.FIELD_SPECS if n not in d.PRODUCTION_FACT_FIELDS]
    fields.append({'field_name':'确认成品数量','type':1})
    monkeypatch.setattr(d.feishu_client,'list_table_fields',lambda *a:fields)
    monkeypatch.setattr(d.feishu_client,'create_field',Mock(side_effect=AssertionError('no migration on conflict')))
    assert not d._sync_unlocked(db_session)['ok']

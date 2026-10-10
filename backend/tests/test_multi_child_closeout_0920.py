from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select
from app.models.order import OrderDetail
from app.services import factory_dispatch_feishu_service as factory
from app.services import order_cost_service as cost, sales_analytics as sales
from app.services import dashboard_monthly_service as monthly, sales_rollup_service as rollup
from app.services import inventory_demand_service as demand
from app.services.order_purchase_facts import UNALLOCATED_CODE, sales_projections
from tests.test_all_product_purchase_cost_0920 import seed
from tests.test_factory_dispatch_field_contract import seed as factory_seed, mock_remote


def report_seed(db):
    order = seed(db, title='入户玄关柜')
    order.status = 'paid'; order.order_date = date.today(); order.is_historical = False
    order.theoretical_cost = D(500); order.actual_cost = D(432)
    for i in range(2):
        line = db.query(OrderDetail).filter_by(sub_order_no=f'CHILD-{i}').one()
        line.product_code = f'PRODUCT-{i}'; line.line_status = 'paid'
    db.commit()
    return order


@pytest.mark.parametrize('order_reversed', [False, True])
def test_unique_legacy_sibling_binding_does_not_hide_other_child(db_session, monkeypatch, order_reversed):
    factory_seed(db_session, ('paid', 'paid'))
    records = [{'record_id':'legacy', 'fields':{'订单号':'TEST-PARENT', '工厂下单号':'畔色100单'}}]
    mock_remote(monkeypatch, records)
    if order_reversed:
        original = factory.build_rows
        monkeypatch.setattr(factory, 'build_rows', lambda db: list(reversed(original(db))))
    result = factory.sync(db_session, include_images=False)
    assert result['ok'], result
    assert result['created'] == 1 and result['verified_entity_count'] == 2
    assert {r['fields']['子订单号'] for r in records} == {'TEST-LINE-0','TEST-LINE-1'}
    second = factory.sync(db_session, include_images=False)
    assert second['ok'] and second['created'] == second['updated'] == 0


def test_summary_retired_without_deleting_attachment_or_receipt(db_session, monkeypatch):
    order = factory_seed(db_session, ('paid', 'paid'))
    summary = OrderDetail(order_no=order.order_no, sub_order_no=order.order_no,
        source='import', qty=1, factory_no=200, factory_delivery_required=True,
        factory_delivery_message_id='existing-sent-proof')
    db_session.add(summary); db_session.commit()
    records = [{'record_id':'old-summary', 'fields':{'订单号':order.order_no,
        '子订单号':order.order_no, '工厂下单号':'畔色200单', '订购数量':1,
        '确认成品数量':1, '工厂下单图':[{'file_token':'preserve'}], '手工备注':'keep'}}]
    mock_remote(monkeypatch, records)
    result = factory.sync(db_session, include_images=False)
    assert result['ok'] and result['expected_entity_count'] == 2
    fields = records[0]['fields']
    assert fields['订单状态'] == '历史母单汇总' and fields['订购数量'] is None
    assert fields['确认成品数量'] is None and fields['手工备注'] == 'keep'
    assert fields['工厂下单图'] == [{'file_token':'preserve'}]
    assert summary.factory_delivery_message_id == 'existing-sent-proof'


def test_ambiguous_legacy_cannot_claim_full_success(db_session, monkeypatch):
    factory_seed(db_session, ('paid','paid'))
    records = [{'record_id':'legacy', 'fields':{'订单号':'TEST-PARENT','工厂下单号':'unknown'}}]
    mock_remote(monkeypatch, records)
    result = factory.sync(db_session, include_images=False)
    assert not result['ok'] and result['verified_entity_count'] == 0
    assert len(records) == 1


@pytest.mark.parametrize('unknown', [None, 0, -1])
def test_incomplete_multi_cost_keeps_every_old_value(db_session, unknown):
    order = report_seed(db_session)
    order.wood_cost_est=D(234); order.est_parts=D(11); db_session.commit()
    line=db_session.query(OrderDetail).filter_by(sub_order_no='CHILD-0').one()
    line.qty=unknown;db_session.commit()
    result=cost.recompute_and_save(db_session,order,ratios={'_store':D('.5')})
    assert not result.resolved and result.cost_incomplete
    assert (order.theoretical_cost,order.wood_cost_est,order.est_parts,order.actual_cost)==(D(500),D(234),D(11),D(432))
    assert cost._multi_product_wood(db_session,order) is None
    assert cost._multi_product_parts(db_session,order) is None


def test_multi_with_service_title_does_not_zero_real_goods(db_session):
    order=report_seed(db_session);order.product_name='桌子,送货入户'
    result=cost.recompute_and_save(db_session,order)
    assert result.resolved and order.theoretical_cost==D(500) and order.actual_cost==D(432)


def test_report_money_once_and_every_child_quantity(db_session):
    order=report_seed(db_session);today=date.today()
    rows=sales.product_breakdown(db_session,start=today,end=today)
    children=[r for r in rows if r['money_pending']]
    assert {(r['sku_code'],r['qty']) for r in children}=={('ANY-0',2),('ANY-1',1)}
    assert sum(r['qty'] for r in rows)==3 and sum(r['revenue'] for r in rows)==D(10000)
    cash=[r for r in rows if r['product_code']==UNALLOCATED_CODE]
    assert len(cash)==1 and cash[0]['qty']==0
    summary=sales.summary(db_session,start=today,end=today)
    assert summary.order_count==1 and summary.revenue==D(10000)
    assert sum(r['cost'] for r in rows)==summary.cost
    assert sum(r['net_profit'] for r in rows)==summary.net_profit


def test_monthly_ranking_rollup_and_forecast_cover_second_product(db_session):
    order=report_seed(db_session);today=date.today()
    mix=monthly.sales_mix(db_session,year=today.year,month=today.month)
    assert mix['total_qty']==3 and mix['total_revenue']==10000
    rank=sales.product_ranking(db_session,metric='qty',period=today.strftime('%Y-%m'))
    assert rank['periods'][0]['total_qty']==3 and rank['periods'][0]['total_revenue']==10000
    assert {r['product_code'] for r in rank['ranking']} >= {'PRODUCT-0','PRODUCT-1'}
    rollup.rollup_day(db_session,today)
    roll=rollup.query_summary(db_session,start=today,end=today)
    assert (roll['order_count'],roll['qty'],roll['revenue'])==(1,3,10000)
    observations=demand.load_observations(db_session,start=today,end=today,product_codes=['PRODUCT-1'])
    assert len(observations)==1 and observations[0].raw_qty==1
    assert observations[0].money_pending and observations[0].sub_order_no=='CHILD-1'
    assert demand.current_unshipped_standard_qty(db_session,'PRODUCT-1')==1


def test_refunded_child_not_reintroduced_by_report_or_stock(db_session):
    report_seed(db_session);today=date.today()
    line=db_session.query(OrderDetail).filter_by(sub_order_no='CHILD-0').one()
    line.line_status='cancelled';line.refund_amount=D(1000);db_session.commit()
    rows=sales.product_breakdown(db_session,start=today,end=today)
    assert sum(r['qty'] for r in rows)==1
    assert all(r['sku_code']!='ANY-0' for r in rows)
    assert demand.current_unshipped_standard_qty(db_session,'PRODUCT-0')==0


def test_projections_never_mutate_database(db_session):
    order=report_seed(db_session)
    before=(order.product_code,order.qty,order.paid_amount,order.actual_cost)
    assert len(sales_projections(db_session,[order]))==3
    assert before==(order.product_code,order.qty,order.paid_amount,order.actual_cost)
    assert not db_session.dirty


def test_cached_sales_invalidated_when_second_child_changes(db_session):
    report_seed(db_session); today=date.today()
    rollup.rollup_day(db_session,today)
    assert rollup.query_summary(db_session,start=today,end=today)['source_verified']
    line=db_session.query(OrderDetail).filter_by(sub_order_no='CHILD-1').one()
    line.qty=3;db_session.flush()
    assert rollup.query_summary(db_session,start=today,end=today)=={}
    rollup.rollup_day(db_session,today)
    assert rollup.query_summary(db_session,start=today,end=today)['qty']==5


def test_exact_summary_does_not_retire_uniquely_bound_legacy_child(db_session,monkeypatch):
    order=factory_seed(db_session,('paid','paid'))
    db_session.add(OrderDetail(order_no=order.order_no,sub_order_no=order.order_no,
        source='import',factory_no=200,factory_delivery_required=True))
    db_session.commit()
    records=[{'record_id':'legacy-child','fields':{'订单号':order.order_no,'工厂下单号':'畔色100单'}}]
    mock_remote(monkeypatch,records)
    result=factory.sync(db_session,include_images=False)
    assert result['ok'] and records[0]['fields']['订单状态']=='生产中'
    assert records[0]['fields']['子订单号']=='TEST-LINE-0'


def test_closeout_is_atomic_source_bound_and_never_touches_bills(db_session,monkeypatch):
    from app.services import multi_child_closeout_service as closeout
    order=report_seed(db_session);today=date.today()
    plan={'plan_sha256':'reviewed','changes':[{'order_no':order.order_no,
        'after':{'theoretical_cost':'501.00'}}], 'unresolved':[],
        'cache_start':str(today),'cache_end':str(today)}
    monkeypatch.setattr(closeout,'prepare',lambda db:plan)
    with pytest.raises(ValueError,match='source_changed'):
        closeout.apply(db_session,'stale')
    assert order.theoretical_cost==D(500)
    result=closeout.apply(db_session,'reviewed')
    assert result['ok'] and result['actual_bills_changed']==0
    assert order.theoretical_cost==D(501) and order.actual_cost==D(432)
    second=closeout.apply(db_session,'reviewed')
    assert second['existing_receipt'] and second['factory_messages_sent']==0


def test_all_refunded_child_cannot_fall_back_to_parent_quantity(db_session):
    order=report_seed(db_session)
    for line in db_session.scalars(select(OrderDetail).where(OrderDetail.order_no==order.order_no)):
        line.line_status='cancelled'
    db_session.commit()
    rows=sales_projections(db_session,[order])
    assert len(rows)==1 and rows[0].qty==0
    assert demand.current_unshipped_standard_qty(db_session,'PRODUCT-0')==0


@pytest.mark.parametrize('custom,title,sku', [(True,'定制家具','定制尺寸'),(False,'商家安装','服务'),(False,'实木桌','PPS123改')])
def test_report_display_identity_cannot_change_financial_rules(db_session,custom,title,sku):
    order=report_seed(db_session)
    order.is_custom=custom;order.product_name=title;order.sku=sku;order.sku_code=sku
    from app.services import order_financials as ofin
    coef=ofin.load_coefficients(db_session)
    expected=sales._profit_for(order,coef,D(0))
    views=sales_projections(db_session,[order])
    totals=tuple(sum((sales._profit_for(v,coef,D(0))[i] for v in views),D(0)) for i in range(4))
    assert totals==expected

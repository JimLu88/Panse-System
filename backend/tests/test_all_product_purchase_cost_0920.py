from decimal import Decimal as D
import pytest
from app.models.order import OrderDetail
from app.models.pricing import PricingSku
from app.services import order_cost_service as cost
from tests.test_order_line_factory_delivery_0812 import _order


def seed(db, title='餐桌', summary=True):
    o = _order(db, 'ALL-PRODUCT'); o.paid_amount=D('10000')
    for i, (pc, wc, ep, qty) in enumerate(((100,60,5,2),(300,150,12,1))):
        db.add(PricingSku(sku_code=f'ANY-{i}', product_code=f'PRODUCT-{i}', sku=f'规格{i}',
                         physical_cost=D(pc),wood_cost=D(wc),external_parts_cost=D(ep)))
        db.add(OrderDetail(order_no=o.order_no,sub_order_no=f'CHILD-{i}',sync_key=f'line:CHILD-{i}',
                           product_name=title,sku_code=f'ANY-{i}',qty=qty,amount=D('1000'),
                           source='import',line_status='signed',refund_status='没有申请退款'))
    if summary:
        db.add(OrderDetail(order_no=o.order_no,sub_order_no=o.order_no,sync_key=f'line:{o.order_no}',
                           product_name=f'{title},{title}',qty=1,amount=D('2000'),source='import'))
    db.commit();return o


@pytest.mark.parametrize('title',['餐桌','床','衣柜','书柜','玄关柜','斗柜','木块小样','升降桌'])
def test_every_product_excludes_parent_summary_from_three_costs(db_session,title):
    o=seed(db_session,title)
    assert cost._multi_product_cost(db_session,o)==D(500)
    assert cost._multi_product_wood(db_session,o)==D(270)
    assert cost._multi_product_parts(db_session,o)==D(22)


def test_refund_excluded_consistently_not_only_theoretical(db_session):
    o=seed(db_session)
    row=db_session.query(OrderDetail).filter_by(sub_order_no='CHILD-0').one()
    row.line_status='cancelled';row.refund_status='退款成功';row.refund_amount=D(1000);db_session.commit()
    assert cost._multi_product_cost(db_session,o)==D(300)
    assert cost._multi_product_wood(db_session,o)==D(150)
    assert cost._multi_product_parts(db_session,o)==D(12)


def test_partial_refund_does_not_drop_whole_product(db_session):
    o=seed(db_session)
    row=db_session.query(OrderDetail).filter_by(sub_order_no='CHILD-0').one()
    row.refund_status='退款成功';row.refund_amount=D(20);db_session.commit()
    assert cost._multi_product_cost(db_session,o)==D(500)
    assert cost._multi_product_wood(db_session,o)==D(270)


def test_all_refunded_is_zero_not_parent_fallback(db_session):
    o=seed(db_session)
    for row in db_session.query(OrderDetail).filter(OrderDetail.sub_order_no.in_(['CHILD-0','CHILD-1'])):
        row.line_status='cancelled'
    db_session.commit()
    assert cost._multi_product_cost(db_session,o)==D(0)
    assert cost._multi_product_wood(db_session,o)==D(0)
    assert cost._multi_product_parts(db_session,o)==D(0)


def test_recompute_and_background_keep_actual_bill(db_session):
    o=seed(db_session);o.actual_cost=D(432);o.theoretical_cost=D(100);db_session.commit()
    cost.auto_cost_backfill(db_session)
    assert o.actual_cost==D(432)
    assert o.theoretical_cost==D(500)
    assert o.wood_cost_est==D(270)


def test_service_line_not_extra_product_or_missing_price(db_session):
    o=seed(db_session)
    db_session.add(OrderDetail(order_no=o.order_no,sub_order_no='SERVICE',sync_key='line:SERVICE',
                              product_name='送货入户',qty=1,source='import'))
    db_session.commit()
    assert cost._multi_product_cost(db_session,o)==D(500)


def test_single_item_parent_id_still_valid(db_session):
    o=seed(db_session,summary=False)
    db_session.query(OrderDetail).filter_by(sub_order_no='CHILD-1').delete()
    row=db_session.query(OrderDetail).filter_by(sub_order_no='CHILD-0').one();row.sub_order_no=o.order_no
    db_session.commit()
    assert cost._purchase_lines_for_cost(db_session,o)==([row],False)


@pytest.mark.parametrize('bad_field,bad_value',[('sku_code','OTHER'),('qty',3),('sub_order_no','OTHER')])
def test_all_product_repair_requires_exact_original_lines(db_session,monkeypatch,bad_field,bad_value):
    from types import SimpleNamespace as NS
    from hashlib import sha256
    from app.services import order_completeness_incident as incident
    from app.models.import_file import ImportedFile
    o=seed(db_session)
    facts=[dict(sub_order_no=f'CHILD-{i}',sku_code=f'ANY-{i}',qty=q,sku=f'规格{i}',product_name='衣柜',status_text='交易成功') for i,q in [(0,2),(1,1)]]
    facts[0][bad_field]=bad_value
    db_session.add(ImportedFile(id=incident.SOURCE_ID,kind='taobao',original_filename='source.xlsx',stored_path='source',file_hash='x',source='test'))
    db_session.commit()
    monkeypatch.setattr(incident,'SOURCE_HASH',sha256(b'test').hexdigest())
    monkeypatch.setattr(incident.import_storage,'read',lambda _:b'test')
    monkeypatch.setattr(incident.imp,'_parse_sales_detail',lambda *args:{o.order_no:NS(lines=facts)})
    result=incident.all_product_financial_plan(db_session)
    assert result['changes']==[] and len(result['unresolved'])==1


def test_all_product_repair_is_atomic_idempotent_and_keeps_actual(db_session,monkeypatch):
    from app.services import order_completeness_incident as incident
    o=seed(db_session);o.actual_cost=D(432);o.theoretical_cost=D(100);db_session.commit()
    lines=db_session.query(OrderDetail).filter(OrderDetail.sub_order_no.in_(['CHILD-0','CHILD-1'])).all()
    plan={'changes':[dict(order_no=o.order_no,before={'theoretical_cost':'100.00','wood_cost_est':str(o.wood_cost_est),'est_parts':str(o.est_parts)},
                         after={'theoretical_cost':'500.00','wood_cost_est':'270.00','est_parts':'22.00'},actual_cost='432.00',paid_amount='10000.00',status=o.status,
                         lines=[(l.id,l.sub_order_no,l.sku_code,l.qty,l.line_status,l.refund_status,str(l.refund_amount)) for l in lines])], 'unresolved':[]}
    monkeypatch.setattr(incident,'all_product_financial_plan',lambda db:plan)
    first=incident.repair_all_product_finance(db_session)
    assert o.theoretical_cost==D(500) and o.actual_cost==D(432)
    assert incident.repair_all_product_finance(db_session)==first

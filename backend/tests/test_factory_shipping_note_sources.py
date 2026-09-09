from datetime import date
import csv,io
from types import SimpleNamespace
import pytest
from app.models.order import Order,OrderDetail
from app.services import taobao_order_import as ti, order_flags as flags
from app.services import factory_dispatch_feishu_service as fd
from app.services import factory_shipping_confirmation_service as confirm
from app.api.orders import factory_production, update_production, ProductionPatch
from fastapi import HTTPException
from tests.test_factory_dispatch_field_contract import seed,mock_remote

def csv_data(tags=('延迟等通知',), present=True, key='TAG-TEST'):
    output=io.StringIO()
    w=csv.writer(output)
    header=['子订单编号','主订单编号','商品标题','购买数量','订单创建时间','订单状态','主订单买家留言','商家备注']
    if present: header.append('备注标签')
    w.writerow(header)
    for i,tag in enumerate(tags):
        row=[f'{key}-{i}',key,'实木桌',1,'2026-09-01','买家已付款，等待卖家发货','等通知发货','开始制作']
        if present:row.append(tag)
        w.writerow(row)
    return output.getvalue().encode('utf-8-sig')

def parse(raw):return ti._parse_sales_detail('report.csv',raw,ti.TaobaoImportReport())

def test_import_tags_presence_clear_alias_and_human_preservation(db_session):
    rep=ti.TaobaoImportReport()
    ti._commit_orders(db_session,parse(csv_data()),'淘宝',rep)
    o=db_session.query(Order).filter_by(order_no='TAG-TEST').one()
    assert o.platform_remark_tags=='延迟等通知'
    assert o.buyer_message=='等通知发货'
    o.remark='人工备注不得覆盖'
    o.customer_shipping_month='2026-12'
    o.customer_shipping_month_source='用户确认'
    db_session.commit()
    ti._commit_orders(db_session,parse(csv_data(present=False)),'淘宝',rep)
    assert o.platform_remark_tags=='延迟等通知'
    ti._commit_orders(db_session,parse(csv_data(('加急备货出',))),'淘宝',rep)
    assert o.platform_remark_tags=='加急备货出'
    ti._commit_orders(db_session,parse(csv_data(('',))),'淘宝',rep)
    assert o.platform_remark_tags==''
    at=o.platform_remark_tags_updated_at
    ti._commit_orders(db_session,parse(csv_data(('',))),'淘宝',rep)
    assert o.platform_remark_tags_updated_at==at
    assert o.remark=='人工备注不得覆盖'
    assert o.customer_shipping_month=='2026-12'
    assert o.customer_shipping_month_source=='用户确认'
    assert flags.waits_for_shipping_notice(o)

@pytest.mark.parametrize('tags,expected',[(('', '延迟等通知'),'延迟等通知'),(('延迟等通知',''),'延迟等通知'),(('延迟等通知','延迟等通知'),'延迟等通知'),(('',''),'')])
def test_multirow_blank_is_not_whole_order_clear(tags,expected):
    assert parse(csv_data(tags))['TAG-TEST'].platform_remark_tags==expected

def test_conflicting_nonempty_tags_are_preserved_and_reported():
    rep=ti.TaobaoImportReport()
    parsed=ti._parse_sales_detail('report.csv',csv_data(('加急备货出','延迟等通知')),rep)
    o=parsed['TAG-TEST']
    assert o.platform_remark_tags=='加急备货出\n延迟等通知'
    assert rep.warnings and flags.platform_shipping_hold(o)

def test_qianniu_tag_and_buyer_alias():
    from openpyxl import Workbook
    wb=Workbook();wb.active.title='订单报表'
    wb.active.append(['订单编号','备注标签']);wb.active.append(['M','延迟等通知'])
    ws=wb.create_sheet('销售明细')
    ws.append(['主订单编号','子订单编号','商品标题','主订单买家留言','备注标签'])
    ws.append(['M','M-1','桌子','注意联系',''])
    ws.append(['M','M-2','桌子','注意联系',''])
    buf=io.BytesIO();wb.save(buf)
    o=ti._parse_qianniu_multi(buf.getvalue(),ti.TaobaoImportReport())[0]['M']
    assert o.platform_remark_tags=='延迟等通知' and o.buyer_message=='注意联系'

def pair(db):
    for key,(month,num) in confirm.CONFIRMED.items():
        db.add(Order(platform='淘宝',order_no=key,status='paid',factory_no=num,qty=1,
            order_date=date(2026,6,1),ship_deadline=date(2026,8,31),
            seller_memo='开始制作' if num==379 else '白色岩板全榉木带电力轨道',
            production_note='人工生产备注',remark='人工保留',paid_amount=1000))
        db.add(OrderDetail(order_no=key,sub_order_no=key,source='import',factory_no=num,
            factory_delivery_required=True,line_status='paid',product_name='桌子',qty=1,amount=1000))
    db.commit()

def test_exact_month_pair_is_atomic_idempotent_and_preserves_identity(db_session,monkeypatch):
    pair(db_session)
    monkeypatch.setattr(confirm,'_archive_tags',lambda db:dict(zip(confirm.CONFIRMED,('加急备货出','延迟等通知'))))
    r=confirm.apply_confirmed_months(db_session)
    assert r['changed_orders']==2 and not r['sync_called']
    assert confirm.apply_confirmed_months(db_session)['changed_orders']==0
    for o in db_session.query(Order):
        month,num=confirm.CONFIRMED[o.order_no]
        assert o.customer_shipping_month==month and o.customer_delay_deadline is None
        assert o.remark=='人工保留' and o.production_note=='人工生产备注'
        assert o.factory_no==num and flags.factory_label(o)==f'畔色{num}单'
        assert not flags.is_remote(o) and not flags.is_factory_remote(o)
        assert flags.factory_schedule(o)['effective_deadline'] is None
        assert flags.factory_schedule(o,today=date(2027,1,1))['effective_deadline'] is None
        assert flags.waits_for_shipping_notice(o)  # Never automatically released at month start/end.
    rows=fd.build_rows(db_session)
    assert len(rows)==2
    for r in rows:
        assert r['预计发货日期'] is None
        assert r['发货安排']=='做好后等通知发货'
        assert r['客户延期单'] is True
        assert '用户确认' in r['订单备注'] and '平台备注标签' in r['订单备注']
        assert '具体日待确认' in r['订单提醒']

def test_identity_conflict_aborts_before_any_changes(db_session,monkeypatch):
    pair(db_session)
    db_session.query(OrderDetail).first().factory_no=900
    db_session.commit()
    monkeypatch.setattr(confirm,'_archive_tags',lambda db:pytest.fail('must validate first'))
    with pytest.raises(ValueError):confirm.apply_confirmed_months(db_session)
    assert all(o.customer_shipping_month is None for o in db_session.query(Order))

@pytest.mark.parametrize('preserve',[False,True])
@pytest.mark.parametrize('kw',[{'factory_no':None}, {'factory_no':375,'is_remote_ship':True}, {'factory_no':375,'production_note':'暂停生产'}])
def test_month_alone_never_authorizes_production(kw,preserve):
    o=Order(order_no='HOLD',platform='淘宝',status='paid',is_customer_delayed=True,
        customer_shipping_month='2026-12',customer_shipping_preserve_production=preserve,**kw)
    assert not flags.is_customer_delay_activated(o)
    assert flags.is_remote(o) and flags.is_factory_remote(o)

@pytest.mark.parametrize('status', ['shipped','signed'])
def test_terminal_status_and_current_alerts_win_over_historical_delay(db_session,status):
    seed(db_session,(status,),customer_shipping_month='2026-12',is_customer_delayed=True,
         customer_shipping_preserve_production=True,platform_remark_tags='延迟等通知')
    r=fd.build_rows(db_session)[0]
    assert r['订单状态']==('已发货' if status=='shipped' else '已签收')
    assert r['发货安排']==r['订单状态'] and r['订单提醒']==''
    assert '2026年12月' in r['订单备注']  # Historical fact retained without current action.

def test_platform_hold_is_shipping_only_not_new_production_authority(db_session):
    seed(db_session,('paid',),platform_remark_tags='延迟等通知',seller_memo='开始制作')
    r=fd.build_rows(db_session)[0]
    assert r['订单状态']=='客户延期'
    assert r['客户延期单'] and r['发货安排']=='做好后等通知发货'
    assert r['预计发货日期'] is None

def test_full_field_sync_clears_old_date_and_second_preview_is_zero(db_session,monkeypatch):
    pair(db_session)
    records=[{'record_id':str(i),'fields':dict(r,人工列='保留',工厂下单图=[{'file_token':'old'}])}
             for i,r in enumerate(fd.build_rows(db_session))]
    calls=mock_remote(monkeypatch,records)
    monkeypatch.setattr(confirm,'_archive_tags',lambda db:dict(zip(confirm.CONFIRMED,('加急备货出','延迟等通知'))))
    confirm.apply_confirmed_months(db_session)
    r=fd.sync(db_session)
    assert r['ok'] and r['remaining_field_differences']=={}
    assert fd.preview_diff(db_session)['planned_updates']==0
    assert all(r['fields']['人工列']=='保留' and r['fields']['工厂下单图']==[{'file_token':'old'}] for r in records)
    assert all(r['fields']['预计发货日期'] is None for r in records)

def test_existing_date_editor_cannot_override_month(db_session,monkeypatch):
    pair(db_session)
    monkeypatch.setattr(confirm,'_archive_tags',lambda db:dict(zip(confirm.CONFIRMED,('加急备货出','延迟等通知'))))
    confirm.apply_confirmed_months(db_session)
    o=db_session.query(Order).first()
    with pytest.raises(HTTPException):
        update_production(o.id,ProductionPatch(is_remote_ship=True),db_session)
    card=next(r for r in factory_production(product=None,db=db_session) if r['id']==o.id)
    assert card['customer_shipping_month']==o.customer_shipping_month
    assert '具体日待确认' in card['shipping_delay_description']
    assert card['effective_deadline'] is None

"""Manifest/independent expected values were saved before calling production code."""
import csv,io,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pytest
from app.models.order import Order,OrderDetail
from app.services import taobao_order_import as ti, factory_dispatch_feishu_service as fd
from tests.test_factory_dispatch_field_contract import mock_remote

MANIFEST=json.loads((Path(__file__).parent/'fixtures/factory_random_lifecycle_2026091002.json').read_text(encoding='utf-8'))
STATUS={'paid':'买家已付款，等待卖家发货','shipped':'卖家已发货，等待买家确认','signed':'交易成功'}

@pytest.mark.parametrize('case',MANIFEST['cases'],ids=[case['id'] for case in MANIFEST['cases']])
def test_frozen_random_lifecycle(db_session,monkeypatch,case,record_property):
    record_property('seed',MANIFEST['seed']);record_property('sample',json.dumps(case,ensure_ascii=False))
    records=[];mock_remote(monkeypatch,records)
    base=datetime.now(timezone.utc)-timedelta(hours=1)
    for step,frame in enumerate(case['frames']):
        output=io.StringIO();writer=csv.writer(output)
        header=['主订单编号','子订单编号','商品标题','购买数量','订单创建时间','订单状态','主订单买家留言','商家备注','备注标签']
        if len(frame['states'])==1:header+=['物流单号']
        writer.writerow(header)
        for i,status in enumerate(frame['states']):
            row=[case['id'],case['id']+'-'+str(i),'实木桌',1,'2026-09-01',STATUS[status],frame['buyer'],frame['seller'],'延迟等通知' if frame['hold']=='tag' else '']
            if len(frame['states'])==1:row+=[frame['tracking']]
            writer.writerow(row)
        report=ti.import_taobao_orders(db_session,'random.csv',output.getvalue().encode('utf-8-sig'),
            source_observed_at=base+timedelta(seconds=step*10),source_kind='agent_download')
        assert not report.errors
        order=db_session.query(Order).filter_by(order_no=case['id']).one()
        order.factory_no=100;order.customer_shipping_month='2026-12' if frame['hold']=='month' else None
        order.is_customer_delayed=frame['hold']=='month';order.customer_shipping_preserve_production=True
        order.remark='人工保留'
        for i,line in enumerate(db_session.query(OrderDetail).filter_by(order_no=case['id']).order_by(OrderDetail.id)):
            line.factory_no=100+i;line.factory_delivery_required=True
        db_session.commit()
        assert fd.sync(db_session,include_images=False)['ok']
        assert len(records)==len(frame['states'])
        for i,expected in enumerate(frame['expected']):
            fields=next(r['fields'] for r in records if r['fields']['子订单号']==case['id']+'-'+str(i))
            assert fields['订单状态']==expected['status'],(step,frame['event'])
            assert fields['发货安排']==expected['plan'],(step,frame['event'])
            assert fields['工厂下单号']==f'畔色{100+i}单'
            assert '人工保留' in fields['订单备注']
        assert order.buyer_message==frame['buyer'] and order.seller_memo==frame['seller']
        if len(frame['states'])==1:
            assert (records[0]['fields'].get('物流单号') or '')==frame['tracking']
        if step==0:
            for record in records:
                record['fields']['工厂人工列']='保留';record['fields']['工厂下单图']=[{'file_token':'human-image'}]
        assert all(r['fields']['工厂人工列']=='保留' and r['fields']['工厂下单图']==[{'file_token':'human-image'}] for r in records)
        repeat=fd.sync(db_session,include_images=False)
        assert repeat['ok'] and repeat['created']==0 and repeat['updated']==0

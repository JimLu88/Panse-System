from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from app.models.order import Order, OrderDetail
from app.services import factory_size_delivery_service as retry
from app.services import factory_sheet, order_sheet_archive_service as archive
from app.services import factory_dispatch_feishu_service as table, feishu_client, settings_service
from tests.test_variant_size_guard_0920 import setup


def seed(db):
    setup(db)
    order=db.query(Order).filter_by(order_no='P').one()
    order.status='paid'; order.paid_amount=2000
    order.customer_name='测试'; order.customer_phone='13800000000'; order.customer_address='浙江省杭州市测试地址100号'
    order.production_note='保留生产备注'; order.remark='保留客户备注'
    line=OrderDetail(order_no='P',sub_order_no='CHILD',source='import',qty=2,product_code='CAB',
        sku_code='LOWER',sku_name='带抽翻门下柜',factory_no=595,factory_delivery_required=True,
        factory_delivery_state='failed',factory_delivery_error='ValueError: 本SKU专属尺寸未核实，不能用其他规格尺寸发送生产单')
    db.add(line);db.commit()
    return order,line


def test_notice_preserves_notes_and_product_truth(db_session,monkeypatch):
    from app.models.pricing import PricingSku
    order,line=seed(db_session)
    sheet=factory_sheet.build_for_order_line(db_session,order.id,line.id)
    monkeypatch.setattr(archive,'_html_to_png',lambda html,**kw:html)
    html=archive.render_png(sheet)
    assert '尺寸尚未核实' in html and '保留客户备注' in html and '保留生产备注' in html
    assert sheet.size_info is None and sheet.qty==2 and sheet.factory_no==595
    assert order.production_note=='保留生产备注'
    assert db_session.query(PricingSku).filter_by(sku_code='LOWER').one().size_info is None
    projection=table._production_projection(db_session,order,line)
    assert not projection['blocked'] and projection['quantity']==2
    assert projection['size']=='尺寸尚未核实'
    assert '尺寸尚未核实' in projection['fields']['生产事实状态']


@pytest.mark.parametrize('placeholder', ['待定', '未核实', '尺寸尚未核实', '   '])
def test_placeholder_is_not_a_verified_dimension(db_session,placeholder):
    from app.models.pricing import PricingSku
    order,line=seed(db_session)
    db_session.query(PricingSku).filter_by(sku_code='LOWER').one().size_info=placeholder
    db_session.commit()
    sheet=factory_sheet.build_for_order_line(db_session,order.id,line.id)
    assert sheet.size_info is None and '尺寸尚未核实' in sheet.production_note


@pytest.mark.parametrize('state', ['sent','uncertain','unknown','rendering','sending_image','sending_caption','held','error'])
def test_scan_protects_non_retryable_states(db_session,state):
    order,line=seed(db_session)
    line.factory_delivery_state=state;db_session.commit()
    result=retry.scan(db_session)
    assert result['ready_sub_order_nos']==[]


@pytest.mark.parametrize('evidence', ['factory_delivery_message_id','factory_delivery_sent_at'])
def test_receipt_protects_even_failed_state(db_session,evidence):
    order,line=seed(db_session)
    setattr(line,evidence,'msg' if evidence.endswith('id') else datetime.now(timezone.utc))
    db_session.commit()
    assert retry.scan(db_session)['ready_sub_order_nos']==[]


def test_manifest_requires_unchanged_scope_and_keeps_quantity_gate(db_session,monkeypatch):
    order,line=seed(db_session)
    manifest=retry.scan(db_session)
    assert manifest['ready_sub_order_nos']==['CHILD']
    line.qty=3;db_session.commit()
    monkeypatch.setattr(archive,'reconcile_order_line_delivery',lambda *a,**k:pytest.fail('must not send drift'))
    with pytest.raises(ValueError,match='scope changed'):retry.apply_manifest(db_session,manifest)
    line.sku_name='定制补差';db_session.commit()
    assert retry.scan(db_session)['ready_sub_order_nos']==[]


def test_exact_retry_sends_once_and_keeps_factory_number(db_session,monkeypatch):
    order,line=seed(db_session)
    settings_service.set_value(db_session,'feishu_push_chat_id','test-chat')
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY',raising=False)
    rendered=[]; sent=[]
    monkeypatch.setattr(archive,'_html_to_png',lambda html,**kw:rendered.append(html) or b'fake-image')
    monkeypatch.setattr(feishu_client,'upload_image',lambda *a:'image-key')
    monkeypatch.setattr(feishu_client,'send_image',lambda *a:sent.append(1) or {'message_id':'image-receipt'})
    monkeypatch.setattr(feishu_client,'reply_card',lambda *a:{'message_id':'card-receipt'})
    monkeypatch.setattr(archive,'archive_sent_line_snapshot',lambda *a,**k:None)
    from app.services import factory_quantity_request_service as quantities
    monkeypatch.setattr(quantities,'complete_pending_receipts',lambda *a,**k:None)
    monkeypatch.setattr(quantities,'complete_delivery',lambda *a,**k:None)
    manifest=retry.scan(db_session)
    result=retry.apply_manifest(db_session,manifest)
    assert result['pushed']==1 and len(sent)==1
    assert '尺寸尚未核实' in rendered[0] and '隔板上柜 900' not in rendered[0]
    assert line.factory_no==595 and line.qty==2 and line.factory_delivery_message_id=='image-receipt'
    again=retry.apply_manifest(db_session,manifest)
    assert again['pushed']==0 and len(sent)==1

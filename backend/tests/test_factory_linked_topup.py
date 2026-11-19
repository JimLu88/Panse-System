from decimal import Decimal
from datetime import date
import pytest
from sqlalchemy import select
from app.models.order import Order,OrderDetail,FactoryOrder
from app.models.pricing import PricingSku
from app.models.settings import SystemSetting
from app.services import factory_linked_topup_service as link
from app.services import factory_sheet,order_sheet_archive_service as sheets,order_line_delivery_service as delivery
from tests.test_order_post_push_audit import exported, remote
from tests.test_order_line_factory_delivery_0812 import _feishu

DIMS={'length':2000,'depth':900,'height':750}

@pytest.fixture
def pair(db_session):
    db=db_session
    main=Order(order_no='MAIN',platform='淘宝',status='paid',qty=1,paid_amount=Decimal('3162.47'),
        buyer_payable_amount=Decimal('3162.47'),is_custom=True,order_date=date(2026,11,19),
        product_code='PPS24210070901',sku_code='PPS2421007090115',sku='砂白2.0米',
        seller_memo='确认定制2米x90。补差价56元',production_note='用户通知开始制作，做好后等客户通知发货，发货前联系客户确认')
    top=Order(order_no='TOP',platform='淘宝',status='paid',qty=1,paid_amount=Decimal('56.00'),
        buyer_payable_amount=Decimal('56.00'),seller_memo='确认定制2米x90。补差价56元')
    a=OrderDetail(order_no='MAIN',sub_order_no='MAIN',source='import',sku_code='PPS2421007090115',
        product_code='PPS24210070901',sku_name='砂白2.0米',qty=1,amount=Decimal('3162.47'),line_status='paid',factory_delivery_required=True)
    b=OrderDetail(order_no='TOP',sub_order_no='TOP',source='import',sku_code='PPS2421007090199',
        product_code='PPS24210070901',sku_name='尺寸定制',qty=1,amount=Decimal('56.00'),line_status='paid',factory_delivery_required=True)
    sku=PricingSku(sku_code=a.sku_code,product_code=a.product_code,sku='砂白2.0米',size_info='长2000 深850 高750 mm')
    extra=PricingSku(sku_code=b.sku_code,product_code=b.product_code,sku='尺寸定制',is_custom_placeholder=True)
    assoc=FactoryOrder(factory_order_no='COST-TOP',platform_order_no='TOP',factory_cost_type='same_order_topup',related_primary_order_no='MAIN')
    db.add_all([main,top,a,b,sku,extra,assoc]);db.commit()
    return main,top,a,b,sku,assoc

def confirm(db,**changes):
    preview=link.preview(db,main_order_no='MAIN',topup_order_no='TOP',dimensions_mm=DIMS)
    args=dict(main_order_no='MAIN',topup_order_no='TOP',dimensions_mm=DIMS,
        expected_identity_sha256=preview['identity_sha256'],actor='01-user-instruction',evidence_ref='explicit:one-table')
    return link.confirm(db,**(args|changes))

def test_exact_binding_releases_only_main_preserves_financials_and_dimensions(db_session,pair):
    m,t,a,b,sku,assoc=pair
    assert sheets._is_parts_topup(db_session,m)[0]
    first=confirm(db_session)
    assert confirm(db_session)==first
    assert delivery.line_is_factory_eligible(db_session,a,m)
    assert not delivery.line_is_factory_eligible(db_session,b,t)
    sheet=factory_sheet.build_for_order_line(db_session,m.id,a.id)
    assert sheet.size_info=='长2000 × 深900 × 高750 mm' and sheet.qty==1 and sheet.is_custom_variant
    assert '不用于本次生产' in sheet.reference_image_notice
    assert sheet.quantity_confirmation['physical_qty']==1
    assert '深900' in sheets.render_html(sheet) and '已确认成品 1 件' in sheets.render_html(sheet)
    assert factory_sheet.build(db_session,m.id).size_info==sheet.size_info
    assert sku.size_info=='长2000 深850 高750 mm' and m.paid_amount==Decimal('3162.47') and t.paid_amount==56
    assert m.seller_memo=='确认定制2米x90。补差价56元' and not a.factory_delivery_message_id and not b.factory_no

@pytest.mark.parametrize('change',['main_sku','topup_sku','main_qty','topup_qty','main_money','topup_money',
    'main_note','topup_note','main_refund','topup_refund','association','standard_size','customer','new_child'])
def test_fact_changes_invalidate_and_never_fall_back_to_standard_size(db_session,pair,change):
    m,t,a,b,sku,assoc=pair;confirm(db_session)
    if change=='main_sku':a.sku_code='OTHER'
    elif change=='topup_sku':b.sku_code='OTHER'
    elif change=='main_qty':a.qty=2
    elif change=='topup_qty':b.qty=2
    elif change=='main_money':m.paid_amount=3000
    elif change=='topup_money':t.paid_amount=57
    elif change=='main_note':m.seller_memo='新备注'
    elif change=='topup_note':t.seller_memo='新备注'
    elif change=='main_refund':m.status='cancelled'
    elif change=='topup_refund':b.refund_status='退款成功'
    elif change=='association':assoc.related_primary_order_no='OTHER'
    elif change=='standard_size':sku.size_info='新尺寸'
    elif change=='customer':m.customer_phone='changed'
    else:db_session.add(OrderDetail(order_no='MAIN',sub_order_no='SECOND',source='import',qty=1,sku_code=a.sku_code))
    db_session.commit()
    assert link.assessment(db_session,'MAIN')['state']=='stale'
    assert sheets._is_parts_topup(db_session,m)[0]
    assert sheets._is_parts_topup(db_session,t)[0]
    sheet=factory_sheet.build_for_order_line(db_session,m.id,a.id)
    assert '待重新确认' in sheet.size_info
    with pytest.raises(ValueError):sheets.render_png(sheet)

@pytest.mark.parametrize('change',['unlinked','closed','unknown_send','placeholder_main','missing_actor','changed_fingerprint','changed_dimensions'])
def test_confirm_rejects_incomplete_or_changed_inputs(db_session,pair,change):
    m,t,a,b,sku,assoc=pair
    p=link.preview(db_session,main_order_no='MAIN',topup_order_no='TOP',dimensions_mm=DIMS)
    args=dict(main_order_no='MAIN',topup_order_no='TOP',dimensions_mm=DIMS,expected_identity_sha256=p['identity_sha256'],actor='user',evidence_ref='request')
    if change=='unlinked':assoc.factory_cost_type='normal'
    elif change=='closed':t.status='cancelled'
    elif change=='unknown_send':a.factory_delivery_state='uncertain'
    elif change=='placeholder_main':sku.is_custom_placeholder=True
    elif change=='missing_actor':args['actor']=''
    elif change=='changed_fingerprint':args['expected_identity_sha256']='bad'
    else:args['dimensions_mm']={**DIMS,'depth':850}
    db_session.commit()
    with pytest.raises(ValueError):link.confirm(db_session,**args)
    assert not db_session.scalar(select(SystemSetting).where(SystemSetting.key==link.PREFIX+'MAIN'))

def test_audit_keeps_blocked_physical_main_visible_and_excludes_only_fee(db_session,pair):
    m,t,a,b,sku,assoc=pair
    assert link.audit_review_needed(db_session,m,a)
    assert not link.audit_review_needed(db_session,t,b)
    confirm(db_session)
    assert not link.audit_review_needed(db_session,m,a)
    t.seller_memo='changed';db_session.commit()
    assert link.audit_review_needed(db_session,m,a)

def test_binding_does_not_release_shipping_hold(db_session,pair):
    from app.services import order_flags
    m,*_=pair;confirm(db_session)
    assert order_flags.is_activated(m)
    assert order_flags.waits_for_shipping_notice(m)


def test_audit_reports_main_then_sends_only_main_once(db_session,pair,exported,remote,_feishu,monkeypatch):
    from app.services import order_post_push_audit as audit, settings_service
    m,t,a,b,sku,assoc=pair
    monkeypatch.setattr('app.services.feishu_client.reply_card',lambda *a,**k:{'message_id':'card-test'})
    settings_service.set_value(db_session,'feishu_push_chat_id','test')
    m.customer_address='测试市测试路1号'
    db_session.commit()
    batch,_=exported([(o.order_no,l.sub_order_no,l.sku_code,1,'买家已付款,等待卖家发货','没有申请退款',0)
        for o,l in [(m,a),(t,b)]])
    held=audit.audit(db_session,batch_id=batch)
    assert any(r['reason']=='linked_topup_main_needs_review' for r in held['remaining']),held
    assert not _feishu
    confirm(db_session)
    first=audit.audit(db_session,batch_id=batch)
    assert first['ok'] and first['images_pushed']==first['verified_count']==1,str(first)
    assert a.factory_delivery_state=='sent' and not b.factory_delivery_message_id and not b.factory_no
    second=audit.audit(db_session,batch_id=batch)
    assert second['ok'] and second['images_pushed']==0,second
    assert len(_feishu)==1


def test_factory_projection_uses_custom_size_and_blocks_stale_binding(db_session,pair):
    from app.services import factory_dispatch_feishu_service as table
    m,t,a,*_=pair
    confirm(db_session)
    result=table._production_projection(db_session,m,a)
    assert result['quantity']==1 and result['size']=='长2000 × 深900 × 高750 mm'
    assert not result['blocked']
    t.seller_memo='已变化';db_session.commit()
    result=table._production_projection(db_session,m,a)
    assert result['blocked'] and '待重新确认' in result['size']

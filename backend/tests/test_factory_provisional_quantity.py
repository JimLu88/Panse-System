import json
from decimal import Decimal
import pytest
from app.models.settings import SystemSetting
from app.services import factory_sheet, factory_dispatch_feishu_service as table
from app.services import factory_provisional_quantity_service as p
from app.services import factory_quantity_request_service as q
from app.services import factory_production_evidence as facts
from app.services import order_sheet_archive_service as sheets
from tests.test_factory_quantity_requests_0920 import flow

@pytest.fixture
def provisional(flow, monkeypatch):
    f=flow
    f.db.add(SystemSetting(key=p.POLICY,value_plain='1',is_secret=False));f.db.commit()
    monkeypatch.setattr(table,'sync',lambda *a,**k:{'ok':True})
    return f

def test_provisional_is_separate_and_purchase_unchanged(provisional):
    f=provisional;s=factory_sheet.build_for_order_line(f.db,f.order.id,f.line.id)
    assert s.qty==1 and s.purchase_qty==2 and s.quantity_provisional and not s.quantity_confirmation
    raw=factory_sheet.build_for_order_line(f.db,f.order.id,f.line.id,allow_provisional=False)
    assert raw.qty==2 and any(w.code=='production_quantity_unverified' for w in raw.warnings)
    proj=table._production_projection(f.db,f.order,f.line)
    assert proj['quantity']==1 and proj['fields']['确认成品数量'] is None
    assert proj['fields']['数量确认状态']=='暂按1件，待确认'
    assert facts.quantity_confirmation(f.db,f.order,f.line) is None

def test_initial_push_then_confirm_updates_same_card(provisional):
    f=provisional
    assert sheets.reconcile_order_line_delivery(f.db,only_sub_order_nos={'CHILD'})['pushed']==1
    assert f.line.factory_delivery_message_id=='CARD' and f.line.factory_delivery_state=='sent'
    assert len(f.calls['cards'])==1 and not f.calls['images'] and f.calls['archives'][-1].qty==1
    f.actual['body']['content']=json.dumps({'text':'@_user_1 3'})
    assert q.handle_reply(f.db,f.event)['state']=='sent'
    assert f.calls['archives'][-1].qty==3
    assert f.line.qty==f.order.qty==2 and f.line.factory_no==513
    assert f.line.factory_delivery_message_id=='CARD' and len(f.calls['cards'])==1 and not f.calls['images']
    assert facts.quantity_confirmation(f.db,f.order,f.line)['physical_qty']==3
    assert q.handle_reply(f.db,f.event)['duplicate'] and len(f.calls['archives'])==2

def test_pending_cards_keep_reminding(provisional,monkeypatch):
    f=provisional;assert p.deliver(f.db,f.line.id)['state']=='sent'
    monkeypatch.setattr(q,'_day',lambda:'2026-11-27')
    monkeypatch.setattr(q.feishu_client,'send_card',lambda *a:{'message_id':'REMINDER'})
    q.remind_pending(f.db)
    r=q._read(q._row(f.db,q.PREFIX+str(f.line.id)))
    assert r['reminder_message_ids']==['REMINDER'] and r['state']=='awaiting_reply'

def test_unknown_patch_resumes_same_card_without_send(provisional,monkeypatch):
    f=provisional;q.request_quantity(f.db,line_id=f.line.id);patch=q.feishu_client.patch_card
    def unknown(*a):raise TimeoutError('timeout')
    monkeypatch.setattr(q.feishu_client,'patch_card',unknown)
    assert p.deliver(f.db,f.line.id)['state']=='update_pending'
    assert len(f.calls['cards'])==1 and not f.calls['archives']
    monkeypatch.setattr(q.feishu_client,'patch_card',patch);q.complete_pending_receipts(f.db)
    assert f.line.factory_delivery_state=='sent' and len(f.calls['cards'])==1 and not f.calls['images']

def test_confirmation_rejects_changed_identity(provisional):
    f=provisional;p.deliver(f.db,f.line.id);f.order.remark='new specification';f.db.commit()
    assert q.handle_reply(f.db,f.event)['state']=='held'
    assert facts.quantity_confirmation(f.db,f.order,f.line) is None and len(f.calls['archives'])==1

def test_table_failure_retries_only_table(provisional,monkeypatch):
    f=provisional;monkeypatch.setattr(table,'sync',lambda *a,**k:{'ok':False})
    assert p.deliver(f.db,f.line.id)['table']['state']=='pending'
    before=len(f.calls['patches']);monkeypatch.setattr(table,'sync',lambda *a,**k:{'ok':True})
    q.complete_pending_receipts(f.db)
    assert len(f.calls['archives'])==1 and len(f.calls['patches'])==before
    assert not q._read(q._row(f.db,q.PREFIX+str(f.line.id)))['factory_sync_pending']

def test_regular_quantity_and_existing_delivery_not_reinterpreted(provisional):
    f=provisional;f.line.sku_name='标准款';f.order.is_custom=False;f.line.product_name='餐桌';f.db.commit()
    s=factory_sheet.build_for_order_line(f.db,f.order.id,f.line.id)
    assert s.qty==2 and s.quantity_provisional is None
    f.line.factory_delivery_message_id='OLD_IMAGE';f.line.factory_delivery_state='sent';f.db.commit()
    assert p.deliver(f.db,f.line.id)['state']!='sent' and not f.calls['patches']

def test_shipping_only_is_never_defaulted(provisional):
    f=provisional;f.order.platform_field_state={'shipping_only':True};f.db.commit()
    assert factory_sheet.build_for_order_line(f.db,f.order.id,f.line.id).quantity_provisional is None
    assert p.deliver(f.db,f.line.id)['state']!='sent' and not f.calls['archives']

def test_bom_rebuilt_for_one(provisional,monkeypatch):
    f=provisional;original=factory_sheet.build_from_fields
    def build(db,**kwargs):
        s=original(db,**kwargs)
        s.materials=[factory_sheet.FactorySheetMaterial('M','木料',Decimal(4),Decimal(4)*kwargs['qty'],'个')]
        return s
    monkeypatch.setattr(factory_sheet,'build_from_fields',build)
    s=factory_sheet.build_for_order_line(f.db,f.order.id,f.line.id)
    assert s.qty==1 and s.materials[0].total_qty==4

def test_card_has_image_and_confirmation(provisional):
    f=provisional;p.deliver(f.db,f.line.id)
    r=q._read(q._row(f.db,q.PREFIX+str(f.line.id)));card=q.question_card(r,choosing=True)
    assert '暂按1件' in json.dumps(card,ensure_ascii=False)
    assert any(e['tag']=='img' for e in card['elements']) and 'select_static' in json.dumps(card)


def test_callback_during_pending_patch_does_not_disable_card(provisional,monkeypatch):
    f=provisional;q.request_quantity(f.db,line_id=f.line.id)
    def unknown(*a):raise TimeoutError()
    monkeypatch.setattr(q.feishu_client,'patch_card',unknown);p.deliver(f.db,f.line.id)
    assert q.handle_reply(f.db,f.event)['state']=='updating'
    assert q._read(q._row(f.db,q.PREFIX+str(f.line.id)))['state']=='awaiting_reply'

def test_unknown_confirmation_update_resumes_same_quantity(provisional,monkeypatch):
    f=provisional;p.deliver(f.db,f.line.id);patch=q.feishu_client.patch_card
    def unknown(*a):raise TimeoutError()
    monkeypatch.setattr(q.feishu_client,'patch_card',unknown)
    f.actual['body']['content']=json.dumps({'text':'@_user_1 4'})
    assert q.handle_reply(f.db,f.event)['state']=='update_pending'
    assert facts.quantity_confirmation(f.db,f.order,f.line)['physical_qty']==4
    monkeypatch.setattr(q.feishu_client,'patch_card',patch);q.complete_pending_receipts(f.db)
    assert f.calls['archives'][-1].qty==4 and len(f.calls['cards'])==1

def test_same_delivery_is_idempotent(provisional):
    f=provisional;p.deliver(f.db,f.line.id)
    assert p.deliver(f.db,f.line.id)['duplicate'] and len(f.calls['archives'])==1

def test_expired_card_replacement_once(provisional,monkeypatch):
    f=provisional
    class Expired(Exception):code=230031
    def patch(db,mid,card):
        if mid=='CARD':raise Expired()
        f.calls['patches'].append((mid,card))
    monkeypatch.setattr(q.feishu_client,'patch_card',patch)
    monkeypatch.setattr(q.feishu_client,'reply_card',lambda *a:{'message_id':'REPLACEMENT'})
    assert p.deliver(f.db,f.line.id)['state']=='sent'
    assert f.line.factory_delivery_message_id=='REPLACEMENT'
    assert p.deliver(f.db,f.line.id)['duplicate']

import json
from datetime import date
from types import SimpleNamespace
import pytest
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.services import factory_quantity_request_service as q
from app.services import factory_production_evidence as facts
from app.services import feishu_bot_service as bot
from app.services import order_sheet_archive_service as sheets


@pytest.fixture
def flow(db_session, monkeypatch):
    from app.services import factory_dispatch_feishu_service
    monkeypatch.setattr('app.services.order_post_push_audit.audit', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(factory_dispatch_feishu_service, 'sync_if_enabled', lambda db: {'ok':True,'errors':[]})
    db = db_session
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY', raising=False)
    o = Order(platform='淘宝', order_no='MAIN', order_date=date(2026,9,20), status='paid',
              qty=2, product_name='定制餐桌', product_code='TABLE', sku_code='CUSTOM',
              sku='尺寸定制', remark='150*75尺寸', customer_address='完整测试地址')
    line = OrderDetail(order_no='MAIN', sub_order_no='CHILD', sync_key='line:CHILD', source='import',
                       product_code='TABLE', product_name='定制餐桌', sku_code='CUSTOM',
                       sku_name='尺寸定制', qty=2, factory_no=513,
                       factory_delivery_required=True, factory_delivery_state='failed')
    db.add_all([o,line, SystemSetting(key='feishu_push_chat_id', value_plain='ERP', is_secret=False)])
    db.commit()
    calls = {'cards': [], 'notes': [], 'images': [], 'patches': [], 'archives': []}
    def card(db, chat, card):
        calls['cards'].append((chat,card)); return {'message_id':'CARD'}
    def note(db, mid, text):
        calls['notes'].append((mid,text)); return {'message_id':f"NOTE{len(calls['notes'])}"}
    monkeypatch.setattr(q.feishu_client, 'send_card', card)
    monkeypatch.setattr(q.feishu_client, 'reply_text', note)
    monkeypatch.setattr(q.feishu_client, 'patch_card', lambda *a: calls['patches'].append(a[1:]))
    monkeypatch.setattr(q.feishu_client, 'get_user_name', lambda *a: '订单负责人')
    monkeypatch.setattr(sheets, '_addr_ok_for_factory', lambda *a: True)
    monkeypatch.setattr(sheets, 'render_png', lambda sheet: f'qty={sheet.qty}'.encode())
    monkeypatch.setattr(q.feishu_client, 'upload_image', lambda *a:'KEY')
    def image(db,chat,key):
        calls['images'].append((chat,key)); return {'message_id':'IMAGE'}
    monkeypatch.setattr(q.feishu_client, 'send_image', image)
    monkeypatch.setattr(sheets, 'archive_sent_line_snapshot', lambda *a, **kw:calls['archives'].append(kw['rendered_sheet']))
    actual = {'message_id':'REPLY', 'parent_id':'CARD', 'root_id':'CARD', 'chat_id':'ERP',
              'deleted':False, 'msg_type':'text', 'sender':{'sender_type':'user','id':'USER'},
              'body':{'content':json.dumps({'text':'@_user_1 1'})}}
    monkeypatch.setattr(q.feishu_client, '_req', lambda *a,**k: {'items':[actual]})
    event = {'message':{'message_id':'REPLY','parent_id':'CARD','root_id':'CARD','chat_id':'ERP',
                        'message_type':'text','content':json.dumps({'text':'1'})}}
    return SimpleNamespace(db=db, order=o, line=line, calls=calls, actual=actual, event=event)


def ask(f):
    return q.request_quantity(f.db,line_id=f.line.id)


def test_changed_notes_renew_held_card_and_reject_old_reply(flow, monkeypatch):
    f=flow; ask(f)
    f.order.remark='新的生产备注'; f.db.commit()
    assert q.handle_reply(f.db,f.event)['state']=='held'
    monkeypatch.setattr(q.feishu_client,'send_card', lambda *a: {'message_id':'CARD2'})
    assert ask(f)['card_message_id']=='CARD2'
    saved=q._read(q._row(f.db,q.PREFIX+str(f.line.id)))
    assert saved['revision']==2 and saved['superseded'][0]['card_message_id']=='CARD'
    assert 'reply_message_id' not in saved and not f.calls['images']
    assert q.handle_reply(f.db,f.event)['state']=='message_unverified'
    assert ask(f)['card_message_id']=='CARD2'


def test_unknown_card_send_never_retried_even_after_notes_change(flow, monkeypatch):
    f=flow
    def unknown(*a): raise TimeoutError('unknown')
    monkeypatch.setattr(q.feishu_client,'send_card',unknown)
    assert ask(f)['state']=='ask_uncertain'
    f.order.remark='new';f.db.commit()
    monkeypatch.setattr(q.feishu_client,'send_card',lambda *a: pytest.fail('unknown replay'))
    assert ask(f)['state']=='ask_uncertain'


def test_question_is_once_non_production_and_bound(flow):
    f=flow
    assert ask(f)['state']=='awaiting_reply'
    assert ask(f)['card_message_id']=='CARD'
    assert len(f.calls['cards'])==1 and not f.calls['images']
    text=json.dumps(f.calls['cards'][0],ensure_ascii=False)
    for marker in ['请勿生产','CHILD','原购买数量','150*75','@机器人']:
        assert marker in text
    assert '完整测试地址' not in text


def test_failed_report_audit_notifies_reply_once_without_replaying_image(flow, monkeypatch):
    monkeypatch.setattr('app.services.order_post_push_audit.audit',
                        lambda *a, **k: {'ok': False, 'remaining_count': 1})
    f = flow
    ask(f)
    result = q.handle_reply(f.db, f.event)
    assert not result['post_push_audit']['ok']
    assert len(f.calls['images']) == 1
    assert f.calls['notes'][-1][0] == 'REPLY'
    assert '核对仍有缺项' in f.calls['notes'][-1][1]
    q.handle_reply(f.db, f.event)
    assert len(f.calls['images']) == 1 and len(f.calls['notes']) == 2


@pytest.mark.parametrize('qty',[1,2,3])
def test_real_reply_confirms_only_child_and_sends_explanation(flow,qty):
    f=flow;ask(f)
    f.actual['body']['content']=json.dumps({'text':f'@_user_1 {qty}'})
    result=bot.on_message_event(f.db,f.event)
    assert result['state']=='sent'
    assert f.line.qty==2 and f.order.qty==2 and f.line.factory_no==513
    assert f.calls['archives'][0].qty==qty
    assert len(f.calls['images'])==1 and len(f.calls['notes'])==1
    assert f'实际成品 {qty} 件' in f.calls['notes'][0][1]
    assert q.handle_reply(f.db,f.event)['duplicate']
    assert len(f.calls['images'])==1 and len(f.calls['notes'])==1


@pytest.mark.parametrize('failure',['failed','unknown','disabled'])
def test_table_problem_never_replays_sent_image(flow,monkeypatch,failure):
    from app.services import factory_dispatch_feishu_service as dispatch
    f=flow;ask(f);calls=[]
    def sync(db):
        calls.append(1)
        if failure=='unknown':raise TimeoutError('table receipt lost')
        return {'ok':failure=='disabled','skipped':'auto_disabled' if failure=='disabled' else None,'errors':['table failure']}
    monkeypatch.setattr(dispatch,'sync_if_enabled',sync)
    q.handle_reply(f.db,f.event)
    record=json.loads(q._row(f.db,q.PREFIX+str(f.line.id)).value_plain)
    assert record['factory_sync']['state']==failure
    assert f.line.factory_delivery_state=='sent' and len(f.calls['images'])==1
    q.handle_reply(f.db,f.event)
    q.complete_pending_receipts(f.db)
    assert calls==[1] and len(f.calls['images'])==1
    assert '工厂表尚未确认同步' in f.calls['notes'][0][1]


def test_guard_automatically_asks_without_sending_image(flow):
    f=flow
    result=sheets.reconcile_order_line_delivery(f.db,limit=1,only_sub_order_nos={'CHILD'})
    assert result['pushed']==0 and result['failures'][0]['quantity_question']['state']=='awaiting_reply'
    assert len(f.calls['cards'])==1 and not f.calls['images']
    sheets.reconcile_order_line_delivery(f.db,limit=1,only_sub_order_nos={'CHILD'})
    assert len(f.calls['cards'])==1


@pytest.mark.parametrize('text',['0','-1','1或2','大概2','2.5','513','1件'])
def test_strict_quantity_parser(flow,text):
    f=flow;ask(f);f.actual['body']['content']=json.dumps({'text':text})
    result=q.handle_reply(f.db,f.event)
    if text in ('513','1件'):
        assert result['state']=='sent'  # Explicit positive integer, never amount-derived.
    else:
        assert result['state']=='invalid_quantity' and not f.calls['images']
        assert facts.quantity_confirmation(f.db,f.order,f.line) is None
        q.handle_reply(f.db,f.event)
        assert len(f.calls['notes'])==1


@pytest.mark.parametrize('fault',['wrong_chat','event_chat','wrong_parent','deleted','bot','missing_sender','wrong_mid'])
def test_untrusted_or_unrelated_reply_cannot_confirm(flow,fault):
    f=flow;ask(f)
    if fault=='wrong_chat':f.actual['chat_id']='OTHER'
    if fault=='event_chat':f.event['message']['chat_id']='OTHER'
    if fault=='wrong_parent':f.actual.update(parent_id='OTHER',root_id='OTHER')
    if fault=='deleted':f.actual['deleted']=True
    if fault=='bot':f.actual['sender']['sender_type']='app'
    if fault=='missing_sender':f.actual['sender']={}
    if fault=='wrong_mid':f.actual['message_id']='OTHER'
    assert q.handle_reply(f.db,f.event)['state']=='message_unverified'
    assert facts.quantity_confirmation(f.db,f.order,f.line) is None and not f.calls['images']


def test_free_number_is_not_a_quantity_reply(flow):
    f=flow;ask(f);f.event['message'].update(parent_id=None,root_id=None)
    assert q.handle_reply(f.db,f.event) is None
    assert not f.calls['images']


def test_uses_official_body_not_forged_event_quantity(flow):
    f=flow;ask(f);f.event['message']['content']=json.dumps({'text':'99'})
    q.handle_reply(f.db,f.event)
    assert facts.quantity_confirmation(f.db,f.order,f.line)['physical_qty']==1


@pytest.mark.parametrize('change',['sku','qty','remark','cancel','refund','sent','unknown'])
def test_changed_or_closed_order_is_held(flow,change):
    f=flow;ask(f)
    if change=='sku':f.line.sku_code='OTHER'
    if change=='qty':f.line.qty=3
    if change=='remark':f.order.remark='changed'
    if change=='cancel':f.order.status='cancelled'
    if change=='refund':f.line.refund_status='退款成功'
    if change=='sent':f.line.factory_delivery_state='sent'
    if change=='unknown':f.line.factory_delivery_state='uncertain'
    f.db.commit()
    assert q.handle_reply(f.db,f.event)['state']=='held'
    assert not f.calls['images']


def test_conflicting_second_answer_cannot_change_sent_order(flow):
    f=flow;ask(f);q.handle_reply(f.db,f.event)
    f.event['message']['message_id']='REPLY2';f.actual['message_id']='REPLY2'
    f.actual['body']['content']=json.dumps({'text':'2'})
    assert q.handle_reply(f.db,f.event)['state']=='conflicting_reply'
    assert facts.quantity_confirmation(f.db,f.order,f.line)['physical_qty']==1
    assert len(f.calls['images'])==1


def test_post_with_mention_and_digit(flow):
    f=flow;ask(f);f.event['message']['message_type']='post'
    f.actual.update(msg_type='post',body={'content':json.dumps({'title':'','content':[[{'tag':'at','user_id':'BOT'},{'tag':'text','text':'2'}]]})})
    assert q.handle_reply(f.db,f.event)['physical_qty']==2


def test_card_timeout_is_not_replayed(flow,monkeypatch):
    f=flow
    def fail(*a):raise TimeoutError('unknown')
    monkeypatch.setattr(q.feishu_client,'send_card',fail)
    assert ask(f)['state']=='ask_uncertain'
    monkeypatch.setattr(q.feishu_client,'send_card',lambda *a:pytest.fail('do not replay'))
    assert ask(f)['state']=='ask_uncertain'


def test_caption_failure_never_resets_image_sent(flow,monkeypatch):
    f=flow;ask(f)
    def fail(*a):raise TimeoutError('unknown')
    monkeypatch.setattr(q.feishu_client,'reply_text',fail)
    assert q.handle_reply(f.db,f.event)['state']=='sent'
    assert f.line.factory_delivery_message_id=='IMAGE'
    assert q.handle_reply(f.db,f.event)['duplicate'] and len(f.calls['images'])==1


def test_image_timeout_preserves_unknown_and_no_replay(flow,monkeypatch):
    f=flow;ask(f)
    def fail(*a):raise TimeoutError('unknown')
    monkeypatch.setattr(q.feishu_client,'send_image',fail)
    assert q.handle_reply(f.db,f.event)['state']=='uncertain'
    assert q.handle_reply(f.db,f.event)['duplicate']
    assert not any('正式制单图已发送' in text for _,text in f.calls['notes'])


def test_notification_disabled_has_no_side_effect(flow,monkeypatch):
    f=flow;monkeypatch.setenv('PANSE_DISABLE_NOTIFY','1')
    assert ask(f)['state']=='notify_disabled' and not f.calls['cards']


@pytest.mark.parametrize('caption_failure', [False, True])
def test_image_receipt_before_crash_resumes_only_explanation(flow, monkeypatch, caption_failure):
    f=flow;ask(f)
    row=q._row(f.db,q.PREFIX+str(f.line.id));record=q._read(row)
    record.update(state='confirmed',reply_message_id='REPLY',physical_qty=1,actor='feishu:USER')
    f.line.factory_delivery_state='sent';f.line.factory_delivery_message_id='IMAGE'
    q._save(f.db,row,record)
    if caption_failure:
        monkeypatch.setattr(q.feishu_client, 'reply_text', lambda *a: (_ for _ in ()).throw(TimeoutError()))
    q.complete_pending_receipts(f.db,only_sub_order_nos={'OTHER'})
    assert not f.calls['notes']
    q.complete_pending_receipts(f.db,only_sub_order_nos={'CHILD'})
    q.complete_pending_receipts(f.db,only_sub_order_nos={'CHILD'})
    assert len(f.calls['notes']) == (0 if caption_failure else 1) and not f.calls['images']
    assert f.calls['patches'][-1][1]['header']['template'] == 'green'
    assert '正式图已发送' in json.dumps(f.calls['patches'][-1][1], ensure_ascii=False)


@pytest.mark.parametrize('qty',[None,0,-2])
def test_missing_or_invalid_purchase_quantity_never_defaults_to_one(flow,qty):
    f=flow
    f.line.qty=qty;f.line.sku_name='普通组合柜';f.order.sku='普通组合柜'
    f.db.commit()
    result=sheets.reconcile_order_line_delivery(f.db,limit=1,only_sub_order_nos={'CHILD'})
    assert result['pushed']==0 and len(f.calls['cards'])==1 and not f.calls['images']
    assert '原购买数量：未知' in json.dumps(f.calls['cards'],ensure_ascii=False)
    f.actual['body']['content']=json.dumps({'text':'2'})
    assert q.handle_reply(f.db,f.event)['state']=='sent'
    assert f.line.qty==qty and f.order.qty==2
    assert f.calls['archives'][0].qty==2 and f.calls['archives'][0].purchase_qty==qty
    assert '原购买数量 未知，实际成品 2 件' in f.calls['notes'][0][1]


def card_event(f, op='factory_quantity_confirm', option='2', mid='CARD'):
    return {'_factory_forward_trusted': True, 'operator': {'open_id': 'USER'},
            'context': {'open_message_id': mid, 'open_chat_id': 'ERP'},
            'action': {'value': {'op': op, 'line_id': f.line.id, 'revision': 1}, 'option': option}}


def test_button_selection_then_confirm_updates_card_before_delivery(flow, monkeypatch):
    f=flow;ask(f)
    card=f.calls['cards'][0][1]
    assert card['header']['title']['content'].endswith('待确认数量')
    assert card['elements'][-1]['actions'][0]['text']['content']=='选择并确认数量'
    q.handle_card(f.db,card_event(f,op='factory_quantity_choose'))
    assert f.calls['patches'][-1][1]['elements'][-1]['actions'][0]['tag']=='select_static'
    assert not f.calls['images'] and facts.quantity_confirmation(f.db,f.order,f.line) is None
    def delivery(db,**kw):
        assert f.calls['patches'][-1][1]['header']['template']=='green'
        assert '已确认数量' in f.calls['patches'][-1][1]['header']['title']['content']
        return {'pushed':0,'reason':'not_sent_yet'}
    monkeypatch.setattr(sheets,'reconcile_order_line_delivery',delivery)
    result=q.handle_card(f.db,card_event(f))
    assert result['physical_qty']==2
    assert facts.quantity_confirmation(f.db,f.order,f.line)['physical_qty']==2
    assert 'actions' not in json.dumps(f.calls['patches'][-1][1])
    assert f.line.qty==2 and f.order.qty==2


@pytest.mark.parametrize('fault',['untrusted','missing_actor','wrong_card','wrong_line','wrong_revision','wrong_chat','invalid_option'])
def test_card_action_validates_origin_binding_and_quantity(flow,fault):
    f=flow;ask(f);event=card_event(f)
    if fault=='untrusted':event['_factory_forward_trusted']=False
    if fault=='missing_actor':event['operator']={}
    if fault=='wrong_card':event['context']['open_message_id']='OTHER'
    if fault=='wrong_line':event['action']['value']['line_id']=9999
    if fault=='wrong_revision':event['action']['value']['revision']=2
    if fault=='wrong_chat':event['context']['open_chat_id']='OTHER'
    if fault=='invalid_option':event['action']['option']='0'
    with pytest.raises(ValueError):q.handle_card(f.db,event)
    assert facts.quantity_confirmation(f.db,f.order,f.line) is None
    assert not f.calls['images']


def test_card_double_click_and_conflicting_selection_do_not_resend(flow):
    f=flow;ask(f)
    assert q.handle_card(f.db,card_event(f))['state']=='sent'
    assert q.handle_card(f.db,card_event(f))['duplicate']
    assert q.handle_card(f.db,card_event(f,option='3'))['state']=='conflicting_reply'
    assert len(f.calls['images'])==1
    assert facts.quantity_confirmation(f.db,f.order,f.line)['physical_qty']==2


def test_pending_reminder_next_day_all_cards_confirm_together(flow,monkeypatch):
    f=flow;monkeypatch.setattr(q,'_day',lambda:'2026-11-19');ask(f)
    monkeypatch.setattr(q,'_day',lambda:'2026-11-20')
    def card(*args):
        f.calls['cards'].append(args[1:]);return {'message_id':'REMINDER'}
    monkeypatch.setattr(q.feishu_client,'send_card',card)
    assert ask(f)['reminded']
    assert not ask(f).get('reminded') and len(f.calls['cards'])==2
    assert q.handle_card(f.db,card_event(f,mid='REMINDER'))['state']=='sent'
    for mid in ['CARD','REMINDER']:
        assert any(m==mid and c['header']['template']=='green' for m,c in f.calls['patches'])
    monkeypatch.setattr(q,'_day',lambda:'2026-11-21')
    q.remind_pending(f.db)
    assert len(f.calls['cards'])==2 and len(f.calls['images'])==1


def test_reminder_does_not_invalidate_original_reply(flow,monkeypatch):
    f=flow;monkeypatch.setattr(q,'_day',lambda:'2026-11-19');ask(f)
    monkeypatch.setattr(q,'_day',lambda:'2026-11-20')
    monkeypatch.setattr(q.feishu_client,'send_card',lambda *a:{'message_id':'REMINDER'})
    ask(f)
    assert q.handle_reply(f.db,f.event)['state']=='sent'
    assert len(f.calls['images'])==1


def test_lost_reminder_receipt_is_not_replayed_on_later_day(flow,monkeypatch):
    f=flow;monkeypatch.setattr(q,'_day',lambda:'2026-11-19');ask(f)
    monkeypatch.setattr(q,'_day',lambda:'2026-11-20')
    def timeout(*a):raise TimeoutError('lost')
    monkeypatch.setattr(q.feishu_client,'send_card',timeout)
    assert ask(f)['reminder_state']=='unknown'
    monkeypatch.setattr(q,'_day',lambda:'2026-11-21')
    monkeypatch.setattr(q.feishu_client,'send_card',lambda *a:pytest.fail('unknown replay'))
    q.remind_pending(f.db)
    assert q.handle_reply(f.db,f.event)['state']=='sent'


def test_pending_scan_does_not_skip_cards_beyond_image_limit(flow,monkeypatch):
    f=flow;monkeypatch.setattr(q,'_day',lambda:'2026-11-19');ask(f)
    monkeypatch.setattr(q,'_day',lambda:'2026-11-20')
    monkeypatch.setattr(q.feishu_client,'send_card',lambda *a:{'message_id':'REMINDER'})
    sheets.reconcile_order_line_delivery(f.db,limit=0)
    record=q._read(q._row(f.db,q.PREFIX+str(f.line.id)))
    assert record['reminder_message_ids']==['REMINDER']
    assert not f.calls['images']


@pytest.mark.parametrize('change',['cancelled','refunded','identity','sent','other_confirmation'])
def test_reminders_exclude_no_longer_pending_orders(flow,monkeypatch,change):
    f=flow;monkeypatch.setattr(q,'_day',lambda:'2026-11-19');ask(f)
    if change=='cancelled':f.order.status='cancelled'
    if change=='refunded':f.line.refund_status='退款成功'
    if change=='identity':f.line.qty=4
    if change=='sent':f.line.factory_delivery_state='sent'
    if change=='other_confirmation':facts.confirm_quantity(f.db,line_id=f.line.id,expected_sku='CUSTOM',expected_purchase_qty=2,physical_qty=1,actor='user',evidence_ref='manual')
    f.db.commit();monkeypatch.setattr(q,'_day',lambda:'2026-11-20')
    monkeypatch.setattr(q.feishu_client,'send_card',lambda *a:pytest.fail('not pending'))
    q.remind_pending(f.db)


def test_failed_card_projection_retries_without_image_replay(flow,monkeypatch):
    f=flow;ask(f)
    monkeypatch.setattr(q.feishu_client,'patch_card',lambda *a:(_ for _ in ()).throw(TimeoutError()))
    assert q.handle_card(f.db,card_event(f))['state']=='sent'
    row=q._row(f.db,q.PREFIX+str(f.line.id));assert q._read(row)['card_sync_pending']
    monkeypatch.setattr(q.feishu_client,'patch_card',lambda *a:f.calls['patches'].append(a[1:]))
    q.complete_pending_receipts(f.db)
    assert not q._read(row)['card_sync_pending']
    assert len(f.calls['images'])==1 and f.calls['patches'][-1][1]['header']['template']=='green'


def test_existing_cards_upgrade_does_not_send_or_confirm(flow):
    f=flow;ask(f)
    facts.confirm_quantity(f.db,line_id=f.line.id,expected_sku='CUSTOM',expected_purchase_qty=2,physical_qty=3,actor='operator',evidence_ref='manual')
    result=q.refresh_existing_cards(f.db)
    assert result=={'updated':1,'pending':0}
    assert not f.calls['images'] and len(f.calls['cards'])==1
    assert f.calls['patches'][-1][1]['header']['template']=='green'
    assert '3 件' in json.dumps(f.calls['patches'][-1][1],ensure_ascii=False)


def test_webhook_routes_only_trusted_quantity_events(flow,monkeypatch):
    import threading
    calls=[]
    class Worker:
        def __init__(self,**kwargs):calls.append(kwargs)
        def start(self):pass
    monkeypatch.setattr(threading,'Thread',Worker)
    event=card_event(flow)
    result=bot.on_card_action(flow.db,event)
    assert result['toast']['type']=='info' and calls[0]['target']==q.run_card_action
    event['_factory_forward_trusted']=False
    assert bot.on_card_action(flow.db,event)['toast']['type']=='warning'
    assert len(calls)==1


def test_sdk_routes_quantity_selection_and_acknowledges(flow,monkeypatch):
    pytest.importorskip('lark_oapi')
    from app.services import feishu_ws_service as ws
    calls=[]
    class Worker:
        def __init__(self,**kwargs):calls.append(kwargs)
        def start(self):pass
    monkeypatch.setattr(ws.threading,'Thread',Worker)
    event=card_event(flow);event.pop('_factory_forward_trusted')
    response=ws._on_card({'event':event})
    assert type(response).__name__=='P2CardActionTriggerResponse'
    assert calls[0]['target']==q.run_card_action
    assert calls[0]['args'][0]['_factory_forward_trusted'] is True
    assert calls[0]['args'][0]['action']['option']=='2'

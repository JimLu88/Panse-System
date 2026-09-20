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


def test_question_is_once_non_production_and_bound(flow):
    f=flow
    assert ask(f)['state']=='awaiting_reply'
    assert ask(f)['card_message_id']=='CARD'
    assert len(f.calls['cards'])==1 and not f.calls['images']
    text=json.dumps(f.calls['cards'][0],ensure_ascii=False)
    for marker in ['请勿生产','CHILD','原购买数量','150*75','@机器人']:
        assert marker in text
    assert '完整测试地址' not in text


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


def test_image_receipt_before_crash_resumes_only_explanation(flow):
    f=flow;ask(f)
    row=q._row(f.db,q.PREFIX+str(f.line.id));record=q._read(row)
    record.update(state='confirmed',reply_message_id='REPLY',physical_qty=1,actor='feishu:USER')
    f.line.factory_delivery_state='sent';f.line.factory_delivery_message_id='IMAGE'
    q._save(f.db,row,record)
    q.complete_pending_receipts(f.db,only_sub_order_nos={'OTHER'})
    assert not f.calls['notes']
    q.complete_pending_receipts(f.db,only_sub_order_nos={'CHILD'})
    q.complete_pending_receipts(f.db,only_sub_order_nos={'CHILD'})
    assert len(f.calls['notes'])==1 and not f.calls['images']

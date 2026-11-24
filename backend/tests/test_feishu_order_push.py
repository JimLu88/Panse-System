import json
from contextlib import nullcontext
import pytest
from app.services import feishu_order_push_service as S, feishu_bot_service as B
from app.services import settings_service as settings, agent_ingest_service as I, platform_scan_service as P


def event(word='推送', mid='m1', **kw):
    e={'_factory_forward_trusted':True, 'sender':{'sender_type':'user','sender_id':{'open_id':'ou_test'}},
       'message':{'message_type':'text','message_id':mid,'chat_id':'oc_test','content':json.dumps({'text':word})}}
    e.update(kw)
    return e

@pytest.fixture
def harness(db_session, monkeypatch):
    settings.set_value(db_session,'feishu_push_chat_id','oc_test')
    db_session.commit()
    launches=[]; replies=[]
    monkeypatch.setattr(S,'_launch',lambda key:launches.append(key))
    monkeypatch.setattr(S,'_reply',lambda *a: replies.append(a) or {'message_id':'reply1'})
    monkeypatch.setattr(I,'is_running',lambda:False)
    return db_session,launches,replies

@pytest.mark.parametrize('word', ['推送','推单','推送订单','补推订单','@_user_1 推送'])
def test_routes_and_deduplicates(harness,word):
    db, launches, replies=harness
    assert B.on_message_event(db,event(word))['started']
    assert S.handle_message(db,event(word))['reason']=='duplicate_message'
    assert not S.handle_message(db,event(mid='m2'))['started']
    assert len(launches)==1

@pytest.mark.parametrize('change', ['untrusted','bot','wrong_chat','no_actor'])
def test_rejects(harness,change):
    db,launches,_=harness; e=event()
    if change=='untrusted': e['_factory_forward_trusted']=False
    if change=='bot': e['sender']['sender_type']='app'
    if change=='wrong_chat': e['message']['chat_id']='elsewhere'
    if change=='no_actor': e['sender']['sender_id']={}
    assert not S.handle_message(db,e)['started']
    assert not launches

@pytest.mark.parametrize('word',['请推送这些','推送了吗','123456','确认数量 3'])
def test_unrelated_untouched(harness,word):
    assert S.handle_message(harness[0],event(word)) is None

def test_busy_and_login_block(harness,monkeypatch):
    db,launches,_=harness
    monkeypatch.setattr(I,'is_running',lambda:True)
    assert not S.handle_message(db,event())['started']
    monkeypatch.setattr(I,'is_running',lambda:False)
    settings.set_value(db,P.ACTIVE_KEY,json.dumps({'state':'running'}));db.commit()
    assert not S.handle_message(db,event(mid='m2'))['started']
    assert not launches

def test_status_no_launch(harness):
    db,launches,replies=harness
    assert S.handle_message(db,event('推送状态'))['kind']=='order_push_status'
    assert not launches and replies

@pytest.mark.parametrize('result,state,text',[
    ({'delivery':{'images_pushed':3}},'completed','3 张'),
    ({'_run_status':'fail','_error':'登录失效'},'blocked','请发送「扫码」'),
    ({'_run_status':'skipped','skipped':'orchestrate_running'},'blocked','尚未完成'),
    ({},'unknown','结果待核对'),
])
def test_worker_results(harness,monkeypatch,result,state,text):
    from app import database
    from app.services import scheduler
    db,launches,replies=harness
    S.handle_message(db,event()); key=launches[0]
    monkeypatch.setattr(database,'SessionLocal',lambda:nullcontext(db))
    calls=[]
    monkeypatch.setattr(scheduler,'_job_web_agent_daily',lambda d:calls.append(1) or result)
    S.run_request(key); S.run_request(key)
    row=S._load(db,key)
    assert row['state']==state and text in row['message']
    assert row['result_notice']=='sent' and len(calls)==1

def test_result_reply_failure_does_not_reexecute(harness,monkeypatch):
    from app import database
    from app.services import scheduler
    db,launches,_=harness
    S.handle_message(db,event());key=launches[0]
    monkeypatch.setattr(database,'SessionLocal',lambda:nullcontext(db))
    monkeypatch.setattr(scheduler,'_job_web_agent_daily',lambda db:{'delivery':{'images_pushed':1}})
    monkeypatch.setattr(S,'_reply',lambda *a: (_ for _ in ()).throw(RuntimeError('network')))
    S.run_request(key); S.run_request(key)
    assert S._load(db,key)['result_notice']=='unknown'
    assert S._load(db,key)['state']=='completed'

def test_rich_post(harness):
    db,launches,_=harness;e=event()
    e['message'].update(message_type='post',content=json.dumps({'zh_cn':{'title':'','content':[[{'tag':'text','text':'推送'}]]}}))
    assert S.handle_message(db,e)['started'] and launches

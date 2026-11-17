import concurrent.futures,json,threading,uuid
from pathlib import Path
import pytest
from campaign_broker import Broker,validate,pipe_server,PIPE

RID='a'*64;JID='b'*64;SID='S-1-5-21-1-2-3-1009'
def message(op='start',**kw):return dict(operation=op,command_id=uuid.uuid4().hex,request_id=RID,**kw)
def policy(sid=SID):return dict(schema='panse-campaign-broker-v1',executor_sid=sid,requests={RID:{'file_sha256':'c'*64}})

@pytest.mark.parametrize('extra',[{'url':'http://localhost:8500/api/settings'},{'file':'C:/secret'},{'acceptance':True},{'code':'x'},{'token':'x'},{'shell':'x'}])
def test_arbitrary_fields_refused(extra):
    with pytest.raises(ValueError):validate(message(**extra),policy())

@pytest.mark.parametrize('op',['settings','exec','dispatch','rotation-run','discover','get-token','delete','acceptance'])
def test_maintenance_commands_not_exposed(op):
    with pytest.raises(ValueError):validate(message(op),policy())

def test_request_scope_and_button_guards():
    with pytest.raises(ValueError):validate(dict(message(),request_id=JID),policy())
    with pytest.raises(ValueError):validate(message('click',job_id=JID,sequence=1,label='确认提交'),policy())
    with pytest.raises(ValueError):validate(message('collect',job_id=JID,sequence=True),policy())
    validate(message('click',job_id=JID,sequence=1,label='商品列表'),policy())

def test_sid_queue_dedup_and_unknown_restart(tmp_path):
    calls=[];gate=threading.Event()
    def dispatch(body,p):calls.append(body);gate.wait(5);return {'ok':True}
    b=Broker(policy(),tmp_path,dispatch);body=message()
    try:
        with pytest.raises(ValueError):b.handle(body,'S-1-5-18')
        assert not calls
        assert b.handle(body,SID)['state']=='queued'
        b.handle(body,SID)
        assert b.handle(message(),SID)['state']=='already_admitted_do_not_replay'
        with pytest.raises(ValueError):b.handle(dict(body,operation='status'),SID)
    finally:gate.set();b.close()
    assert len(calls)==1 and b.status(body['command_id'])['state']=='finished'
    with b.db() as db:db.execute("UPDATE commands SET state='running'")
    b=Broker(policy(),tmp_path,lambda *a:pytest.fail('must not replay'))
    assert b.handle(body,SID)['state']=='unknown';b.close()

def test_backend_timeout_is_unknown_not_retryable(tmp_path):
    def dispatch(*args):raise TimeoutError('could already have submitted')
    b=Broker(policy(),tmp_path,dispatch);body=message()
    b.handle(body,SID);b.close()
    assert b.status(body['command_id'])['state']=='unknown'
    assert b.handle(message(),SID)['state']=='already_admitted_do_not_replay'


@pytest.mark.parametrize('operation',['discover-home','discover-detail','discover-detail-readback','observation'])
def test_new_command_can_read_discovery_again_without_replaying_same_command(tmp_path,operation):
    calls=[];b=Broker(policy(),tmp_path,lambda body,p:calls.append(body) or {'platform_write':False})
    body=dict(operation=operation,command_id=uuid.uuid4().hex)
    if operation in ('discover-detail','discover-detail-readback'):body['title']='official title'
    if operation=='observation':body.update(observation_id='f'*64,offset=0)
    b.handle(body,SID);b.handle(body,SID)
    b.handle(dict(body,command_id=uuid.uuid4().hex),SID);b.close()
    assert len(calls)==2

def test_real_windows_pipe_sid_and_minimum_client_rights(tmp_path):
    import win32api,win32security,win32con,win32file
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
    sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0]);token.Close()
    name=PIPE+'-test-'+uuid.uuid4().hex;ready=threading.Event()
    b=Broker(policy(sid),tmp_path,lambda *a:{'fixture':True})
    with concurrent.futures.ThreadPoolExecutor() as pool:
        task=pool.submit(pipe_server,b,pipe_name=name,once=True,ready=ready)
        assert ready.wait(5)
        handle=win32file.CreateFile(name,0x100003,0,None,3,0x00110000,None)
        try:
            win32file.WriteFile(handle,json.dumps(dict(operation='command-status',command_id='d'*32)).encode())
            try:_,raw=win32file.ReadFile(handle,16384)
            except Exception:
                task.result(15);raise
            assert json.loads(raw)['state']=='not_found'
            win32file.WriteFile(handle,b'1')
        finally:handle.Close()
        task.result(15)
    b.close()


def test_real_powershell_client(tmp_path):
    import subprocess,win32api,win32security,win32con
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
    sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0]);token.Close()
    ready=threading.Event();b=Broker(policy(sid),tmp_path,lambda *a:{'fixture':True})
    with concurrent.futures.ThreadPoolExecutor() as pool:
        task=pool.submit(pipe_server,b,once=True,ready=ready);assert ready.wait(5)
        result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(Path(__file__).parent/'Invoke-Campaign.ps1'),'-Operation','command-status','-CommandId','e'*32],capture_output=True,text=True,timeout=25)
        task.result(15)
    b.close()
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['state']=='not_found'


def test_first_live_mode_uses_only_original_real_acceptance_gate():
    from campaign_broker import initial_live_mode
    assert initial_live_mode(dict(ready=True)) is False
    assert initial_live_mode(dict(ready=False,installed=True,missing=['real_continuous_acceptance'])) is True
    for cap in [dict(ready=False,installed=False),dict(ready=False,installed=True,missing=['login']),dict(ready=False,installed=True,missing=[])]:
        with pytest.raises(ValueError):initial_live_mode(cap)


def test_prepare_registers_new_request_without_editing_security_policy(tmp_path):
    from campaign_broker import prepare_registered,digest,save_immutable
    import hashlib
    from types import SimpleNamespace
    shop='test-shop';url='https://myseller.taobao.com/activity?campaignId=123&unitedActivityId=456'
    def saved(stage,**data):
        doc=dict(request=dict(stage=stage,expected_shop=shop,**({'title':'test activity'} if stage!='home' else {})),platform_write=False,observation=data)
        raw=json.dumps(doc,ensure_ascii=False).encode();oid=hashlib.sha256(raw).hexdigest()
        save_immutable(tmp_path/'observations'/(oid+'.json'),raw);return oid
    home=saved('home',links=[url]);detail=saved('detail',title='test activity',campaign_id='123',phase_id='456')
    page=dict(entry='fixed entry',url=url,campaign_id='123',phase_id='456',sign_record_id='789',shop_id=shop,title='test activity',phase_title='phase',start='2026-11-20 00:00:00',end='2026-11-21 23:59:59',official_rate='.12',evidence_id=detail)
    facts=dict(pages={'123/456/789':page},daily_campaign='123/456/789',coverage={},known_campaigns_complete=True,discovery_id=home)
    captured=[]
    def save(f):
        captured.append(f);rid=digest(f);path=tmp_path/(rid+'.json');path.write_text(json.dumps(f,ensure_ascii=False),'utf-8')
        return dict(request_id=rid,request_path=str(path))
    p=dict(policy(),shop=shop,_state_dir=str(tmp_path));before=json.dumps(p,sort_keys=True)
    result=prepare_registered(facts,p,SimpleNamespace(save_prepared=save))
    assert json.dumps(p,sort_keys=True)==before
    assert (tmp_path/'registered'/(result['request_id']+'.json')).is_file()
    assert captured[0]['pages']['123/456/789']['page_evidence']['sha256']==detail
    b=Broker(p,tmp_path,lambda *a:{'ok':True})
    assert b.handle(dict(message(),request_id=result['request_id']),SID)['state']=='queued';b.close()
    with pytest.raises(ValueError):prepare_registered(dict(facts,command='arbitrary'),p,SimpleNamespace(save_prepared=save))


def test_inspection_publishes_only_original_job_png(tmp_path):
    from campaign_broker import publish_inspection
    import hashlib
    p=tmp_path/'source'/JID/'takeover/2/page.png';p.parent.mkdir(parents=True)
    raw=b'\x89PNG\r\n\x1a\nfixture';p.write_bytes(raw)
    result=dict(command=dict(operation='inspect',state='finished',sequence=2,result=dict(screenshot={'path':str(p),'sha256':hashlib.sha256(raw).hexdigest()})))
    output=publish_inspection(result,RID,JID,tmp_path/'source',tmp_path/'receipts')
    assert Path(output['command']['result']['screenshot']['path']).read_bytes()==raw
    assert result['command']['result']['screenshot']['path']==str(p)
    result['command']['result']['screenshot']['path']=str(tmp_path/'other.png');(tmp_path/'other.png').write_bytes(raw)
    with pytest.raises(ValueError,match='original_job'):publish_inspection(result,RID,JID,tmp_path/'source',tmp_path/'receipts')


def test_real_pipe_keeps_listener_across_clients_and_idle_timeout(tmp_path):
    import time,win32api,win32security,win32con,win32file,win32pipe
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
    sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0]);token.Close()
    name=PIPE+'-sequential-'+uuid.uuid4().hex;ready=threading.Event();b=Broker(policy(sid),tmp_path,lambda *a:{})
    with concurrent.futures.ThreadPoolExecutor() as pool:
        task=pool.submit(pipe_server,b,pipe_name=name,ready=ready,connections=21,idle_timeout_ms=300)
        assert ready.wait(5)
        # Include an idle listener timeout before opening any client.
        time.sleep(.45)
        for i in range(20):
            try:win32pipe.WaitNamedPipe(name,5000)
            except Exception:
                if task.done():task.result()
                raise
            h=win32file.CreateFile(name,0x100003,0,None,3,0x00110000,None)
            try:
                win32file.WriteFile(h,json.dumps(dict(operation='command-status',command_id='a'*32)).encode())
                _,raw=win32file.ReadFile(h,16384);assert json.loads(raw)['state']=='not_found'
                win32file.WriteFile(h,b'1');time.sleep(.005)
            finally:h.Close()
        task.result(5)
    b.close()

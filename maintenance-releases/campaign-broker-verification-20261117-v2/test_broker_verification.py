import json
import pytest
import probe_running_broker as m

SID='S-1-5-21-1-2-3-1009'

class Backend:
    def __init__(self, checkpoint, delay=20):
        self.checkpoint=checkpoint; self.now=0; self.delay=delay; self.cid=None; self.submits=0
        self.terminal='finished'; self.wrong_id=False; self.schema='campaign-same-task-takeover-v1'
    def sleep(self, value):self.now+=value
    def invoke(self, body):
        if body['operation']=='exec':return {'state':'rejected','reason':'unsupported_operation'}
        if body['operation']=='capabilities':
            bound=json.loads(self.checkpoint.read_text())
            assert bound['executor_sid']==bound['token_sid']==SID
            assert bound['command_id']==body['command_id']
            assert bound['phase']=='admission_unknown'
            self.cid=body['command_id'];self.submits+=1
            return dict(state='queued',command_id=self.cid)
        if self.cid is None:return dict(state='not_found',command_id=body['command_id'])
        assert body['command_id']==self.cid
        return dict(command_id='wrong' if self.wrong_id else self.cid,
                    state='running' if self.now<self.delay else self.terminal,
                    result=dict(schema=self.schema,platform_write=False,source_sha256={'source':'abc'}))

def run(tmp_path, backend, **kwargs):
    return m.verify(SID,backend.invoke,tmp_path/'checkpoint.json',tmp_path/'receipt.json',
                    clock=lambda:backend.now,sleep=backend.sleep,**kwargs)

def test_slow_real_read_completes_after_old_fifteen_second_window(tmp_path):
    b=Backend(tmp_path/'checkpoint.json')
    result=run(tmp_path,b,timeout=30)
    assert b.now==20 and b.submits==1
    assert result['command_id']==b.cid and result['maintenance_command_rejected']

def test_timeout_then_resume_same_request_without_submit(tmp_path):
    b=Backend(tmp_path/'checkpoint.json')
    with pytest.raises(TimeoutError,match='resume_same_command'):run(tmp_path,b,timeout=2)
    assert not (tmp_path/'receipt.json').exists()
    cid=b.cid;b.now=30
    assert run(tmp_path,b)['command_id']==cid
    assert b.submits==1

@pytest.mark.parametrize('state',['unknown','failed','rejected'])
def test_terminal_failure_never_generates_receipt(tmp_path,state):
    b=Backend(tmp_path/'checkpoint.json',delay=0);b.terminal=state
    with pytest.raises(ValueError,match='not_verified'):run(tmp_path,b)
    assert not (tmp_path/'receipt.json').exists()

def test_wrong_command_id_rejected(tmp_path):
    b=Backend(tmp_path/'checkpoint.json',delay=0);b.wrong_id=True
    with pytest.raises(ValueError,match='command_identity'):run(tmp_path,b)
    assert not (tmp_path/'receipt.json').exists()

def test_checkpoint_sid_change_rejected_before_ipc(tmp_path):
    p=tmp_path/'checkpoint.json';p.write_text(json.dumps(dict(schema=m.SCHEMA,executor_sid='other',token_sid=SID)))
    with pytest.raises(ValueError,match='checkpoint_identity'):
        m.verify(SID,lambda body:pytest.fail('must not call IPC'),p,tmp_path/'receipt.json')

def test_missing_admitted_request_not_replayed(tmp_path):
    b=Backend(tmp_path/'checkpoint.json')
    with pytest.raises(TimeoutError):run(tmp_path,b,timeout=0)
    b.cid=None
    with pytest.raises(ValueError,match='missing_no_replay'):run(tmp_path,b)
    assert b.submits==1

def test_enabled_account_not_reset_or_disabled(tmp_path,monkeypatch):
    (tmp_path/'preparation-receipt.json').write_text(json.dumps({'sid':SID}))
    monkeypatch.setattr(m,'STAGE',tmp_path)
    monkeypatch.setattr(m.ctypes.windll.shell32,'IsUserAnAdmin',lambda:True)
    monkeypatch.setattr(m.win32security,'LookupAccountName',lambda *args:(m.win32security.ConvertStringSidToSid(SID),'test',1))
    monkeypatch.setattr(m.win32net,'NetUserGetInfo',lambda *args:{'flags':0})
    monkeypatch.setattr(m.win32net,'NetUserSetInfo',lambda *args:pytest.fail('must not change enabled account'))
    with pytest.raises(ValueError,match='active_account'):m.main()

def test_bad_schema_not_accepted(tmp_path):
    b=Backend(tmp_path/'checkpoint.json',delay=0);b.schema='unrelated'
    with pytest.raises(ValueError,match='not_verified'):run(tmp_path,b)
    assert not (tmp_path/'receipt.json').exists()

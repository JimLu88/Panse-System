"""Bounded, resumable real-SID verification; never dispatch platform work."""
from pathlib import Path
import json,secrets,time,uuid,ctypes
from datetime import datetime, timezone
import win32net,win32netcon,win32security,win32con,win32file,win32pipe

STAGE=Path('C:/ProgramData/PanseCampaignExec');NAME='PanseCampaignExec';PIPE=r'\\.\pipe\PanseCampaignExec-v1'
SCHEMA='campaign-broker-verification-checkpoint-v2'

def save(path, value):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2),encoding='utf-8')
    tmp.replace(path)

def call(body):
    win32pipe.WaitNamedPipe(PIPE,5000)
    h=win32file.CreateFile(PIPE,0x100003,0,None,3,0x00110000,None)
    try:
        win32file.WriteFile(h,json.dumps(body).encode());_,raw=win32file.ReadFile(h,131072)
        win32file.WriteFile(h,b'1');return json.loads(raw)
    finally:h.Close()

def call_as(token, body):
    win32security.ImpersonateLoggedOnUser(token)
    try:return call(body)
    finally:win32security.RevertToSelf()

def verify(sid, invoke, checkpoint, receipt, *, timeout=180, clock=time.monotonic, sleep=time.sleep):
    """Checkpoint is admin-only; every IPC call uses the recorded real SID."""
    if checkpoint.exists():
        state=json.loads(checkpoint.read_text('utf-8'))
        if state.get('schema')!=SCHEMA or state.get('executor_sid')!=sid or state.get('token_sid')!=sid:
            raise ValueError('checkpoint_identity_mismatch')
        if not state.get('command_id') or not state.get('maintenance_command_rejected'):
            raise ValueError('checkpoint_binding_incomplete')
    else:
        rejected=invoke(dict(operation='exec',command_id=uuid.uuid4().hex,code='must not execute'))
        if rejected.get('state')!='rejected':raise ValueError('maintenance_command_not_refused')
        state=dict(schema=SCHEMA,executor_sid=sid,token_sid=sid,command_id=uuid.uuid4().hex,
                   maintenance_command_rejected=True,maintenance_rejection=rejected,
                   created_at=datetime.now(timezone.utc).isoformat(),phase='prepared')
        save(checkpoint,state)
    cid=state['command_id']
    result=invoke(dict(operation='command-status',command_id=cid))
    if result.get('state')=='not_found':
        if state['phase']!='prepared':raise ValueError('original_command_missing_no_replay')
        state['phase']='admission_unknown';save(checkpoint,state)
        result=invoke(dict(operation='capabilities',command_id=cid))
        if result.get('command_id')!=cid or result.get('state') not in ('queued','running','finished'):
            raise ValueError('fixed_read_not_admitted')
        state['phase']='admitted';save(checkpoint,state)
    deadline=clock()+timeout
    while result.get('state') in ('queued','running'):
        if clock()>=deadline:
            state['last_state']=result['state'];save(checkpoint,state)
            raise TimeoutError('verification_pending_resume_same_command')
        sleep(min(1,max(0,deadline-clock())))
        result=invoke(dict(operation='command-status',command_id=cid))
    if result.get('command_id')!=cid:raise ValueError('command_identity_changed')
    value=result.get('result',{})
    if result.get('state')!='finished' or value.get('schema')!='campaign-same-task-takeover-v1':
        raise ValueError('installed_backend_read_not_verified')
    if value.get('platform_write') is not False or not value.get('source_sha256'):
        raise ValueError('backend_evidence_incomplete')
    state.update(phase='verified',last_state='finished',verified_at=datetime.now(timezone.utc).isoformat())
    save(checkpoint,state)
    evidence=dict(state='real_executor_sid_broker_verified',executor_sid=sid,token_sid=sid,
                  command_id=cid,fixed_capabilities_read=True,maintenance_command_rejected=True,
                  backend_source_sha256=value['source_sha256'],account_enabled=False,platform_write=False,
                  chat_identity_switched=False,verified_at=state['verified_at'],checkpoint=str(checkpoint))
    save(receipt,evidence)
    return evidence

def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():raise PermissionError('administrator_required')
    prepared=json.loads((STAGE/'preparation-receipt.json').read_text('utf-8-sig'))
    sid=win32security.ConvertSidToStringSid(win32security.LookupAccountName(None,NAME)[0])
    if sid!=prepared['sid']:raise ValueError('account_identity_changed')
    flags=win32net.NetUserGetInfo(None,NAME,1)['flags']
    if not flags&win32netcon.UF_ACCOUNTDISABLE:raise ValueError('active_account_requires_no_password_reset')
    lock=win32file.CreateFile(str(STAGE/'private-state'/'broker-verification.lock'),
                             win32con.GENERIC_READ|win32con.GENERIC_WRITE,0,None,win32con.OPEN_ALWAYS,0,None)
    token=None
    try:
        password='Aa9!'+secrets.token_urlsafe(48)
        try:
            win32net.NetUserSetInfo(None,NAME,1003,{'password':password})
            win32net.NetUserSetInfo(None,NAME,1008,{'flags':flags&~win32netcon.UF_ACCOUNTDISABLE})
            token=win32security.LogonUser(NAME,'.',password,win32con.LOGON32_LOGON_INTERACTIVE,win32con.LOGON32_PROVIDER_DEFAULT)
        finally:
            win32net.NetUserSetInfo(None,NAME,1008,{'flags':flags|win32netcon.UF_ACCOUNTDISABLE});password=None
        token_sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
        if token_sid!=sid:raise ValueError('real_token_identity_mismatch')
        evidence=verify(token_sid,lambda body:call_as(token,body),
                        STAGE/'private-state'/'broker-verification-checkpoint.json',STAGE/'broker-verification.json')
        print(json.dumps(evidence))
    finally:
        if token:token.Close()
        lock.Close()

if __name__=='__main__':main()


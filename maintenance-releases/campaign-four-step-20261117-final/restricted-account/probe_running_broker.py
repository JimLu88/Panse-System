"""One real new-SID read-only broker check; leave account disabled."""
from pathlib import Path
import json,secrets,time,uuid,ctypes
import win32net,win32netcon,win32security,win32con,win32file,win32pipe

STAGE=Path('C:/ProgramData/PanseCampaignExec');NAME='PanseCampaignExec';PIPE=r'\\.\pipe\PanseCampaignExec-v1'

def call(body):
    win32pipe.WaitNamedPipe(PIPE,5000)
    h=win32file.CreateFile(PIPE,0x100003,0,None,3,0x00110000,None)
    try:
        win32file.WriteFile(h,json.dumps(body).encode());_,raw=win32file.ReadFile(h,131072)
        win32file.WriteFile(h,b'1');return json.loads(raw)
    finally:h.Close()

def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():raise PermissionError('administrator_required')
    prepared=json.loads((STAGE/'preparation-receipt.json').read_text('utf-8-sig'))
    if win32security.ConvertSidToStringSid(win32security.LookupAccountName(None,NAME)[0])!=prepared['sid']:raise ValueError('account_identity_changed')
    password='Aa9!'+secrets.token_urlsafe(48);token=None
    flags=win32net.NetUserGetInfo(None,NAME,1)['flags']
    try:
        win32net.NetUserSetInfo(None,NAME,1003,{'password':password})
        win32net.NetUserSetInfo(None,NAME,1008,{'flags':flags&~win32netcon.UF_ACCOUNTDISABLE})
        token=win32security.LogonUser(NAME,'.',password,win32con.LOGON32_LOGON_INTERACTIVE,win32con.LOGON32_PROVIDER_DEFAULT)
    finally:
        win32net.NetUserSetInfo(None,NAME,1008,{'flags':flags|win32netcon.UF_ACCOUNTDISABLE});password=None
    try:
        win32security.ImpersonateLoggedOnUser(token)
        try:
            bad=call(dict(operation='exec',command_id=uuid.uuid4().hex,code='must not execute'))
            if bad.get('state')!='rejected':raise ValueError('maintenance_command_not_refused')
            cid=uuid.uuid4().hex;admitted=call(dict(operation='capabilities',command_id=cid))
            if admitted.get('state')!='queued':raise ValueError('fixed_read_not_admitted')
            for _ in range(30):
                result=call(dict(operation='command-status',command_id=cid))
                if result['state'] not in ('queued','running'):break
                time.sleep(.5)
            if result.get('state')!='finished' or result.get('result',{}).get('schema')!='campaign-same-task-takeover-v1':raise ValueError('installed_backend_read_not_verified')
        finally:win32security.RevertToSelf()
    finally:token.Close()
    receipt=dict(state='real_executor_sid_broker_verified',executor_sid=prepared['sid'],fixed_capabilities_read=True,maintenance_command_rejected=True,
        backend_source_sha256=result['result']['source_sha256'],account_enabled=False,platform_write=False,chat_identity_switched=False)
    (STAGE/'broker-verification.json').write_text(json.dumps(receipt,indent=2),'utf-8')
    print(json.dumps(receipt))

if __name__=='__main__':main()

"""Administrator staging migration. Account remains disabled on every exit."""
from pathlib import Path
import argparse,ctypes,json,secrets,shutil,subprocess,sys,hashlib,uuid,threading,concurrent.futures
import win32api,win32con,win32net,win32netcon,win32security,ntsecuritycon,win32file,pywintypes
from restricted_acl import deny,protect_tree,plain,FULL,no_access

ROOT=Path('D:/AI/畔色ERP系统');STAGE=Path('C:/ProgramData/PanseCampaignExec')
NAME='PanseCampaignExec'
FILES=('campaign_broker.py','Invoke-Campaign.ps1','Activate-RestrictedAccount.ps1')
MUTATING=False

def private(path):
    path.mkdir(exist_ok=True);plain(path)
    current=win32security.GetTokenInformation(win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY),win32security.TokenUser)[0]
    acl=win32security.ACL()
    for who in (current,win32security.ConvertStringSidToSid('S-1-5-32-544'),win32security.ConvertStringSidToSid('S-1-5-18')):
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS,3,FULL,who)
    win32security.SetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION|win32security.PROTECTED_DACL_SECURITY_INFORMATION,None,None,acl,None)

def disabled():
    row=win32net.NetUserGetInfo(None,NAME,1)
    win32net.NetUserSetInfo(None,NAME,1008,{'flags':row['flags']|win32netcon.UF_ACCOUNTDISABLE})

def real_probe(sid):
    # Temporary random password stays in memory. Logon once then disable again;
    # impersonation uses the acquired token; no interactive desktop is created.
    password='Aa9!'+secrets.token_urlsafe(48);token=None
    win32net.NetUserSetInfo(None,NAME,1003,{'password':password})
    try:
        row=win32net.NetUserGetInfo(None,NAME,1)
        win32net.NetUserSetInfo(None,NAME,1008,{'flags':row['flags']&~win32netcon.UF_ACCOUNTDISABLE})
        token=win32security.LogonUser(NAME,'.',password,win32con.LOGON32_LOGON_INTERACTIVE,win32con.LOGON32_PROVIDER_DEFAULT)
    finally:disabled();password=None
    checks=[]
    from campaign_broker import Broker,pipe_server,PIPE
    try:
        actual=win32security.GetTokenInformation(token,win32security.TokenUser)[0]
        if actual!=sid:raise ValueError('probe_wrong_identity')
        ready=threading.Event();pipe_name=PIPE+'-identity-probe-'+uuid.uuid4().hex
        broker=Broker(dict(executor_sid=win32security.ConvertSidToStringSid(sid),requests={}),STAGE/'private-state/pipe-probe',lambda *a:{'fixture_only':True})
        pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        task=pool.submit(pipe_server,broker,pipe_name=pipe_name,once=True,ready=ready)
        if not ready.wait(5):raise ValueError('pipe_probe_not_ready')
        win32security.ImpersonateLoggedOnUser(token)
        try:
            for path,mode,label in [
                (ROOT/'ERP程序/scripts/campaign_continuous_execute.py','rb','source_read_denied'),
                (ROOT/'Web-Agent程序/app/engine/campaign_takeover.py','r+b','source_write_denied'),
                (STAGE/'program/campaign_broker.py','r+b','broker_write_denied'),
                (STAGE/'private-config/policy.json','rb','policy_read_denied'),
                (STAGE/'private-state/probe-secret.txt','rb','secret_fixture_read_denied')]:
                no_access(path,mode);checks.append(label)
            for folder_name in ('inbox','work'):
                path=STAGE/folder_name/('probe-'+uuid.uuid4().hex+'.txt')
                with path.open('x') as f:f.write('permission fixture only')
                path.unlink();checks.append(folder_name+'_write_delete_ok')
            # Ask for DELETE/WRITE_DAC handles, without deleting or changing any live file.
            for path in (ROOT,ROOT.parent,STAGE/'program',ROOT/'Web-Agent程序/app/engine/campaign_takeover.py'):
                for access in (ntsecuritycon.DELETE,ntsecuritycon.WRITE_DAC):
                    try:h=win32file.CreateFile(str(path),access,7,None,3,0x02000000,None)
                    except pywintypes.error as exc:
                        if exc.winerror!=5:raise
                    else:h.Close();raise AssertionError('delete_or_acl_change_allowed')
            checks.append('source_and_parent_replace_acl_denied')
            h=win32file.CreateFile(pipe_name,0x100003,0,None,3,0x00110000,None)
            try:
                win32file.WriteFile(h,json.dumps(dict(operation='command-status',command_id='a'*32)).encode())
                _,data=win32file.ReadFile(h,16384)
                if json.loads(data)['state']!='not_found':raise ValueError('real_identity_pipe_result_changed')
                win32file.WriteFile(h,b'1');checks.append('real_executor_sid_pipe_authorized')
            finally:h.Close()
        finally:
            win32security.RevertToSelf();task.result(20);pool.shutdown();broker.close()
    finally:
        if token:token.Close()
    return checks

def main():
    global MUTATING
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    if not args.apply:print(json.dumps({'scope':'new SID only; ERP source tree, current profile secrets, parent replacement rights, broker staging','account_remains_disabled':True}));return
    if not ctypes.windll.shell32.IsUserAnAdmin():raise PermissionError('administrator_required')
    receipt=json.loads((STAGE/'preparation-receipt.json').read_text('utf-8-sig'));sid=win32security.ConvertStringSidToSid(receipt['sid'])
    if win32security.LookupAccountName(None,NAME)[0]!=sid:raise ValueError('account_sid_changed')
    if (STAGE/'migration-receipt.json').exists():
        print(json.dumps({'state':'already_migrated_no_account_or_acl_changes'}));return
    if not win32net.NetUserGetInfo(None,NAME,1)['flags'] & win32netcon.UF_ACCOUNTDISABLE:raise ValueError('active_account_requires_separate_migration')
    MUTATING=True
    disabled();plain(STAGE);private(STAGE/'private-config');private(STAGE/'private-state')
    src=Path(__file__).resolve().parent
    for name in FILES:
        target=STAGE/'program'/name
        raw=(src/name).read_bytes()
        if target.exists() and target.read_bytes()!=raw:
            if (STAGE/'migration-receipt.json').exists():raise ValueError('completed_staging_must_use_separate_upgrade')
            old=target.read_bytes();backup=STAGE/'private-state'/('staged-'+hashlib.sha256(old).hexdigest()+'.bak')
            if not backup.exists():backup.write_bytes(old)
        target.write_bytes(raw)
        if target.read_bytes()!=raw:raise ValueError('staged_file_hash_mismatch')
    policy=STAGE/'private-config/policy.json'
    expected_policy=dict(schema='panse-campaign-broker-v1',executor_sid=receipt['sid'],shop='畔色木作',requests={})
    if policy.exists():
        old=json.loads(policy.read_text('utf-8'))
        legacy={k:v for k,v in expected_policy.items() if k!='shop'}
        if old not in (legacy,expected_policy):raise ValueError('existing_policy_must_not_be_overwritten')
    policy.write_text(json.dumps(expected_policy,indent=2),'utf-8')
    (STAGE/'private-state/probe-secret.txt').write_text('fixture only; no real credential','utf-8')
    results=[]
    with (STAGE/'private-state/acl-before.jsonl').open('a',encoding='utf-8') as log:
        # Target only the new SID; other users and all existing ACEs are preserved.
        parent_mask=ntsecuritycon.DELETE|ntsecuritycon.FILE_DELETE_CHILD|ntsecuritycon.WRITE_DAC|ntsecuritycon.WRITE_OWNER
        deny(ROOT.parent,sid,parent_mask,journal=log)
        deny(Path('D:/'),sid,ntsecuritycon.FILE_DELETE_CHILD,journal=log)
        print('Protecting ERP tree for the new SID only',flush=True)
        results.append(protect_tree(ROOT,sid,log))
        # User-profile roots inherit a deny; explicit exceptions in credential
        # and interpreter directories are covered with per-object denies.
        profile=Path.home()
        deny(profile,sid,FULL,propagate=True,journal=log)
        for leaf in ('.ssh','.codex','AppData/Local/Programs/Python','AppData/Local/Microsoft/Edge/User Data'):
            path=profile/leaf
            if path.exists():
                print('Protecting credential/interpreter subtree: '+leaf,flush=True)
                results.append(protect_tree(path,sid,log))
    checks=real_probe(sid)
    result=dict(state='staged_acl_and_real_identity_verified',account=NAME,enabled=False,scope_results=results,
        checks=checks,broker_policy_requests=0,host_switched=False,hard_permission_isolation=False,
        production_platform_write=False,credentials_copied=False,ready_for_local_password_and_host_switch=True)
    (STAGE/'migration-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    finally:
        if MUTATING:disabled()

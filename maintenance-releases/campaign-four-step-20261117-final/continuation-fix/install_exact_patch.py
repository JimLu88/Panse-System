"""Install a hash-bound campaign-only source patch after all campaign owners idle.

No browser launch, order task, notification, image build or NAS service change.
The NAS release lock serializes maintenance; backups remain local and immutable.
"""
from pathlib import Path
import argparse,hashlib,json,os,socket,sqlite3,subprocess,sys,time,uuid
import requests
ROOT=Path('D:/AI/畔色ERP系统');OUT=ROOT/'outputs/campaign-four-step-maintenance-20261117/continuation-fix'
PROGRAMS={'ERP':ROOT/'ERP程序','WA':ROOT/'Web-Agent程序'}
SSH=['ssh','-i',str(Path.home()/'.ssh/panse_nas'),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-p','2222','15068803006@DS923plus','sudo','-n','python3','-']


def sha(raw):return hashlib.sha256(raw).hexdigest()


def planned(package):
    result=[]
    for row in package['files']:
        root=PROGRAMS[row['project']].resolve();path=(root/row['path']).resolve()
        if not path.is_relative_to(root):raise ValueError('path_outside_program')
        current=path.read_bytes() if path.exists() else None
        current_sha=sha(current) if current is not None else None
        if current_sha==row['after_sha256']:
            result.append((row,path,current,current,True));continue
        if current_sha!=row['before_sha256']:raise ValueError('runtime_changed:'+row['path'])
        lines=current.decode('utf-8').replace('\r\n','\n').splitlines(True) if current is not None else []
        end=len(lines)+1
        for edit in reversed(row['edits']):
            if not 0<=edit['start']<=edit['end']<end:raise ValueError('invalid_patch_ranges')
            lines[edit['start']:edit['end']]=edit['lines'];end=edit['start']+1
        after=''.join(lines).replace('\n',row['newline']).encode('utf-8')
        if sha(after)!=row['after_sha256']:raise ValueError('reconstructed_source_mismatch:'+row['path'])
        if path.suffix=='.py':compile(after.decode('utf-8-sig'),str(path),'exec')
        result.append((row,path,current,after,False))
    return result


def run_ps(command):
    p=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,timeout=20)
    if p.returncode:raise ValueError('powershell_operation_failed')
    return p.stdout.strip()


def check_idle(http,headers):
    listeners=json.loads(run_ps("@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -in @(8500,8502) } | Select-Object LocalPort,OwningProcess) | ConvertTo-Json -Compress"))
    if isinstance(listeners,dict):listeners=[listeners]
    if not listeners or any(p['LocalPort']==8500 for p in listeners) or not any(p['LocalPort']==8502 for p in listeners):
        raise ValueError('shared_8500_not_confirmed_stopped_no_restart')
    r=http.get('http://127.0.0.1:8502/session/status',headers=headers,timeout=10);r.raise_for_status()
    if r.json().get('browser_open') is not False:raise ValueError('dedicated_browser_not_confirmed_closed')
    for path,table in [(ROOT/'outputs/campaign-continuous/jobs.sqlite3','continuous_jobs'),
                      (PROGRAMS['WA']/'data/output/campaign-transfers/jobs.sqlite','campaign_transfer_jobs')]:
        with sqlite3.connect(path.resolve(strict=True).as_uri()+'?mode=ro',uri=True) as db:
            if db.execute(f"SELECT 1 FROM {table} WHERE state='running'").fetchone():raise ValueError('campaign_writer_running')
            if table=='campaign_transfer_jobs' and db.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_ui_takeovers'").fetchone():
                if db.execute("SELECT 1 FROM campaign_ui_takeovers WHERE state IN ('active','write_unknown')").fetchone():raise ValueError('campaign_takeover_owns_browser')


def nas_lock(owner,release=False):
    script="from pathlib import Path\np=Path('/volume1/docker/panse/.panse-release-lock')\nowner="+repr(owner)+'\n'
    if release:
        script+="assert (p/'owner').read_text()==owner, 'not_this_owner'\n(p/'owner').unlink()\np.rmdir()\nprint('released')\n"
    else:script+="p.mkdir()\n(p/'owner').write_text(owner)\nprint('acquired')\n"
    p=subprocess.run(SSH,input=script,capture_output=True,text=True,timeout=25)
    if p.returncode:raise ValueError('nas_release_lock_not_acquired_or_not_owned')
    return p.stdout.strip()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True);parser.add_argument('--sha256',required=True)
    parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    raw=args.package.read_bytes()
    if sha(raw)!=args.sha256:raise ValueError('package_changed')
    package=json.loads(raw);changes=planned(package)
    if not args.apply:
        print(json.dumps(dict(state='verified_not_installed',files=len(changes),compiled=True,package_sha256=args.sha256)));return
    sys.path.insert(0,str(PROGRAMS['WA']))
    from app.vault.store import get_or_create_api_token
    http=requests.Session();http.trust_env=False;headers={'Authorization':'Bearer '+get_or_create_api_token()}
    check_idle(http,headers)
    owner='02-campaign-four-step-'+uuid.uuid4().hex
    nas_lock(owner);changed=[];host_stopped=False;result={};local_lock=ROOT/'.campaign-maintenance-lock'
    local_owned=False
    try:
        local_lock.mkdir();local_owned=True;(local_lock/'owner').write_text(owner)
        check_idle(http,headers)
        process=json.loads(run_ps("Get-NetTCPConnection -State Listen -LocalPort 8502 | ForEach-Object { Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $_.OwningProcess) } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"))
        if not isinstance(process,dict) or not process['CommandLine'].endswith('-m app.browser.campaign_edge'):
            raise ValueError('dedicated_host_process_identity_mismatch')
        old_pid=int(process['ProcessId'])
        run_ps('Stop-Process -Id '+str(old_pid)+' -ErrorAction Stop');host_stopped=True
        # Validate again immediately before the first file replacement.
        changes=planned(package)
        backup=OUT/'installed-backup'/owner
        for row,path,before,after,already in sorted(changes,key=lambda r:r[2] is not None):
            if already:continue
            if before is not None:
                saved=backup/row['project']/row['path'];saved.parent.mkdir(parents=True,exist_ok=True)
                with saved.open('xb') as stream:stream.write(before)
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_name(path.name+'.'+owner+'.tmp')
            with temporary.open('xb') as stream:stream.write(after)
            os.replace(temporary,path);changed.append((row,path,before,after))
        if any(sha(path.read_bytes())!=row['after_sha256'] for row,path,_,_,_ in changes):raise ValueError('post_install_hash_mismatch')
        logs=OUT/'host-after-install.log'
        with logs.open('ab') as log:
            subprocess.Popen([str(PROGRAMS['WA']/'.venv/Scripts/python.exe'),'-m','app.browser.campaign_edge'],cwd=PROGRAMS['WA'],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
        deadline=time.monotonic()+25
        while True:
            try:
                response=http.get('http://127.0.0.1:8502/healthz',timeout=2)
                if response.status_code==200 and response.json().get('service')=='campaign-edge-session':break
            except requests.ConnectionError:pass
            if time.monotonic()>deadline:raise ValueError('dedicated_host_restart_not_verified')
            time.sleep(.5)
        response=http.post('http://127.0.0.1:8502/session/action',headers=headers,
            json={'operation':'program_takeover_capabilities','options':{}},timeout=20);response.raise_for_status()
        capabilities=response.json()
        if capabilities.get('schema')!='campaign-same-task-takeover-v1':raise ValueError('installed_protocol_not_loaded')
        response=http.get('http://127.0.0.1:8502/session/status',headers=headers,timeout=10);response.raise_for_status()
        if response.json().get('browser_open') is not False:raise ValueError('unexpected_browser_open')
        result=dict(state='installed_and_loaded',package_sha256=args.sha256,files=len(changes),changed_files=len(changed),
            old_edge_pid=old_pid,edge_browser_open=False,capabilities=capabilities,
            api_8500='stopped_will_load_routes_on_next_normal_wake',nas_images_changed=False,
            platform_write=False,platform_acceptance=False,hard_permission_isolation=False,backup=str(backup))
    except BaseException as exc:
        result=dict(state='installation_incomplete',package_sha256=args.sha256,error_type=type(exc).__name__,
            reason=str(exc) if isinstance(exc,ValueError) else 'see_local_install_log',changed_files=len(changed),
            host_stopped=host_stopped,platform_write=False,automatic_retry=False)
        raise
    finally:
        (OUT/'deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
        if local_owned and (local_lock/'owner').read_text()==owner:
            (local_lock/'owner').unlink();local_lock.rmdir()
        nas_lock(owner,release=True)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

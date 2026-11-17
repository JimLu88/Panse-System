"""SID-authenticated local named pipe. No generic shell, URL, path or token API."""
from pathlib import Path
import argparse,concurrent.futures,hashlib,json,re,sqlite3,threading,time
from contextlib import contextmanager

PIPE=r'\\.\pipe\PanseCampaignExec-v1'
MAX_BYTES=16384
READS={'status','takeover-status','capabilities','discover-home','discover-detail','discover-detail-readback','observation'}
TAKEOVER={'acquire','inspect','click','reload','baseline','upload','collect','release'}
BUTTONS={'商品批量导入','批量导入','我知道了','知道了','活动列表','商品列表'}

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def exact(value,pattern):
    return isinstance(value,str) and re.fullmatch(pattern,value) is not None

def validate(body,policy):
    if not isinstance(body,dict):raise ValueError('object_required')
    op=body.get('operation')
    fields={'operation','command_id'}
    if op in ('capabilities','discover-home'):pass
    elif op in ('discover-detail','discover-detail-readback'):fields|={'title'}
    elif op=='prepare':fields|={'facts'}
    elif op=='observation':fields|={'observation_id','offset'}
    elif op in ('start','status','finalize'):fields|={'request_id'}
    elif op=='resume':fields|={'request_id','job_id'}
    elif op=='takeover-status' or op in TAKEOVER:
        fields|={'request_id','job_id'}
        if op not in ('takeover-status','acquire'):fields|={'sequence'}
        if op=='click':fields|={'label'}
    else:raise ValueError('operation_not_exposed')
    if set(body)!=fields:raise ValueError('exact_fields_required')
    if not exact(body['command_id'],'[0-9a-f]{32}'):raise ValueError('command_id_required')
    for key in ('request_id','job_id'):
        if key in fields and not exact(body[key],'[0-9a-f]{64}'):raise ValueError('exact_identity_required')
    if 'request_id' in fields and body['request_id'] not in policy.get('requests',{}):raise ValueError('request_not_authorized')
    if 'sequence' in fields and (type(body['sequence']) is not int or not 1<=body['sequence']<=25):raise ValueError('invalid_sequence')
    if 'label' in fields and body['label'] not in BUTTONS:raise ValueError('button_not_exposed')
    if 'title' in fields and (not isinstance(body['title'],str) or not 1<=len(body['title'])<=200):raise ValueError('exact_title_required')
    if op=='prepare' and not isinstance(body['facts'],dict):raise ValueError('facts_required')
    if op=='observation' and (not exact(body['observation_id'],'[0-9a-f]{64}') or type(body['offset']) is not int or not 0<=body['offset']<=2000000):raise ValueError('observation_reference_invalid')
    return body

class Broker:
    def __init__(self,policy,state,dispatch):
        self.policy=dict(policy);self.state=Path(state);self.dispatch=dispatch
        self.state.mkdir(parents=True,exist_ok=True)
        self.policy['_state_dir']=str(self.state)
        self.lock=threading.Lock();self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, effect TEXT, body TEXT, state TEXT, result TEXT)')
            db.execute("UPDATE commands SET state='unknown',result=? WHERE state IN ('queued','running')",(json.dumps({'reason':'broker_restarted_do_not_replay'}),))
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.state/'commands.sqlite',timeout=5)
        try:
            with db:yield db
        finally:db.close()
    def status(self,cid):
        if not exact(cid,'[0-9a-f]{32}'):raise ValueError('command_id_required')
        with self.db() as db:row=db.execute('SELECT state,result FROM commands WHERE id=?',(cid,)).fetchone()
        return dict(command_id=cid,state=row[0],result=json.loads(row[1] or '{}')) if row else dict(command_id=cid,state='not_found')
    def handle(self,body,peer_sid):
        if peer_sid!=self.policy['executor_sid']:raise ValueError('caller_sid_not_authorized')
        if isinstance(body,dict) and body.get('operation')=='command-status':
            if set(body)!={'operation','command_id'}:raise ValueError('exact_fields_required')
            return self.status(body['command_id'])
        effective=dict(self.policy,requests=dict(self.policy.get('requests',{})))
        for path in (self.state/'registered').glob('*.json'):
            if exact(path.stem,'[0-9a-f]{64}'):effective['requests'][path.stem]=json.loads(path.read_text('utf-8'))
        validate(body,effective)
        cid=body['command_id'];encoded=json.dumps(body,sort_keys=True,ensure_ascii=False)
        effect=digest({k:v for k,v in body.items() if k!='command_id'})
        with self.lock,self.db() as db:
            old=db.execute('SELECT body FROM commands WHERE id=?',(cid,)).fetchone()
            if old:
                if old[0]!=encoded:raise ValueError('command_id_rebound')
                return self.status(cid)
            if body['operation'] not in READS:
                prior=db.execute('SELECT id FROM commands WHERE effect=? LIMIT 1',(effect,)).fetchone()
                if prior:return dict(state='already_admitted_do_not_replay',original_command_id=prior[0])
            if db.execute("SELECT COUNT(*) FROM commands WHERE state IN ('queued','running')").fetchone()[0]>=8:raise ValueError('bounded_queue_full')
            db.execute('INSERT INTO commands VALUES(?,?,?,?,NULL)',(cid,effect,encoded,'queued'))
        self.pool.submit(self.run,cid,body,effective)
        return dict(state='queued',command_id=cid,automatic_retry=False)
    def run(self,cid,body,effective):
        with self.db() as db:db.execute("UPDATE commands SET state='running' WHERE id=?",(cid,))
        try:
            result=self.dispatch(body,effective)
            if len(json.dumps(result,ensure_ascii=False).encode())>MAX_BYTES*8:raise ValueError('response_too_large')
            state='finished'
        except BaseException as exc:
            # A backend timeout can follow a successful write. Never infer rejection or retry.
            state='unknown';result=dict(error_type=type(exc).__name__,reason='backend_result_unconfirmed_read_original_task',automatic_retry=False)
        with self.db() as db:db.execute('UPDATE commands SET state=?,result=? WHERE id=?',(state,json.dumps(result,ensure_ascii=False),cid))
    def close(self):self.pool.shutdown(wait=True)

def production_dispatch(body,policy):
    import sys
    root=Path('D:/AI/畔色ERP系统');wa=root/'Web-Agent程序'
    if str(wa) not in sys.path:sys.path.insert(0,str(wa))
    from app.engine import campaign_operator as operator
    op=body['operation'];rid=body.get('request_id');jid=body.get('job_id')
    state=Path(policy['_state_dir'])
    if op.startswith('discover-'):
        import asyncio
        from app.engine.campaign_discovery_bridge import observe
        stage={'discover-home':'home','discover-detail':'detail','discover-detail-readback':'detail_readback'}[op]
        request=dict(stage=stage,expected_shop=policy['shop'])
        if 'title' in body:request['title']=body['title']
        observed=asyncio.run(observe(request))
        if observed.get('ok') is not True:return observed
        ref=observed['evidence'];raw=Path(ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('captured_evidence_changed')
        save_immutable(state/'observations'/(ref['sha256']+'.json'),raw)
        return dict(state='observed_not_registered',observation_id=ref['sha256'],characters=len(raw.decode('utf-8')),preview=raw.decode('utf-8')[:24000],platform_write=False)
    if op=='observation':
        raw=(state/'observations'/(body['observation_id']+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=body['observation_id']:raise ValueError('captured_evidence_changed')
        value=raw.decode('utf-8');offset=body['offset']
        return dict(observation_id=body['observation_id'],offset=offset,text=value[offset:offset+24000],next_offset=offset+24000 if offset+24000<len(value) else None,platform_write=False)
    if op=='prepare':return prepare_registered(body['facts'],policy,operator)
    if op=='capabilities':return operator.takeover_call('capabilities',{})
    if op=='status':return operator.status(rid)
    binding=policy['requests'][rid]
    # Policy points only to an immutable prepared request in the fixed project directory.
    source=root/'outputs/campaign-continuous/prepared'/(rid+'.json')
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=binding['file_sha256']:raise ValueError('approved_request_file_changed')
    request=json.loads(raw)
    if digest(request)!=rid:raise ValueError('approved_request_identity_changed')
    if op=='start':
        from app.engine.campaign_continuous_client import start
        from app.engine.campaign_continuous_worker import capabilities
        cap=capabilities();first=initial_live_mode(cap)
        # An explicit start is real authorized business. The original first-run
        # mode executes it but cannot fabricate an acceptance receipt.
        result=start(request,acceptance=first)
        return dict(result,first_live_acceptance_used=first,
            acceptance_completion='same_entry_finalize_after_real_terminal' if first else 'already_verified')
    if op=='finalize':
        from app.engine.campaign_continuous_client import local_session
        from app.vault.store import get_or_create_api_token
        with local_session() as http:
            response=http.post('http://127.0.0.1:8500/api/campaign/continuous/acceptance/'+rid+'/finalize',
                headers={'Authorization':'Bearer '+get_or_create_api_token()},timeout=(5,30),allow_redirects=False)
            response.raise_for_status();return response.json()
    from app.engine.campaign_takeover import original_action
    original_action(rid,jid)  # Cannot use another request's job or arbitrary status lookup.
    if op=='resume':return operator.resume_original(rid,jid)
    options={'job_id':jid}
    if op=='acquire':options['request_id']=rid
    if 'sequence' in body:options['sequence']=body['sequence']
    if 'label' in body:options['label']=body['label']
    result=operator.takeover_call('status' if op=='takeover-status' else op,options)
    if op=='takeover-status':
        result=publish_inspection(result,rid,jid,wa/'data/output/campaign-transfers',state.parent/'receipts')
    return result

def publish_inspection(result,rid,jid,source_root,receipt_root):
    from copy import deepcopy
    command=result.get('command') or {}
    if command.get('operation')!='inspect' or command.get('state')!='finished':return result
    screenshot=(command.get('result') or {}).get('screenshot')
    if not screenshot:return result
    seq=command.get('sequence')
    if type(seq) is not int or not 1<=seq<=24:raise ValueError('inspection_sequence_invalid')
    expected=(Path(source_root)/jid/'takeover'/str(seq)/'page.png').resolve(strict=True)
    if Path(screenshot['path']).resolve(strict=True)!=expected:raise ValueError('inspection_file_not_original_job')
    if expected.stat().st_size>8*1024*1024:raise ValueError('inspection_image_too_large')
    raw=expected.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    if not raw.startswith(b'\x89PNG\r\n\x1a\n') or sha!=screenshot['sha256']:raise ValueError('inspection_image_changed')
    target=Path(receipt_root)/rid/jid/(sha+'.png');save_immutable(target,raw)
    result=deepcopy(result);result['command']['result']['screenshot']=dict(path=str(target),sha256=sha,source='verified_same_job_inspection')
    return result

def initial_live_mode(cap):
    if cap.get('ready') is True:return False
    if cap.get('installed') is True and cap.get('missing')==['real_continuous_acceptance']:return True
    raise ValueError('runtime_not_installed_or_unrecognized_gate')

def save_immutable(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('xb') as out:out.write(raw)
    except FileExistsError:
        if path.read_bytes()!=raw:raise ValueError('immutable_business_record_changed')

def prepare_registered(facts,policy,operator):
    from copy import deepcopy
    allowed={'pages','daily_campaign','coverage','known_campaigns_complete','discovery_id'}
    if set(facts)!=allowed or facts.get('known_campaigns_complete') is not True:raise ValueError('exact_business_facts_required')
    state=Path(policy['_state_dir'])
    def observation(oid):
        if not exact(oid,'[0-9a-f]{64}'):raise ValueError('captured_observation_required')
        raw=(state/'observations'/(oid+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=oid:raise ValueError('captured_evidence_changed')
        doc=json.loads(raw)
        if doc['request']['expected_shop']!=policy['shop'] or doc.get('platform_write') is not False:raise ValueError('observation_shop_changed')
        return doc,raw.decode('utf-8')
    home,home_text=observation(facts['discovery_id'])
    if home['request']['stage']!='home':raise ValueError('official_home_required')
    pages=deepcopy(facts['pages']);links=[]
    if not isinstance(pages,dict) or not pages or len(pages)>30:raise ValueError('bounded_activity_pages_required')
    keys={'entry','url','campaign_id','phase_id','sign_record_id','shop_id','title','phase_title','start','end','official_rate','evidence_id'}
    for page in pages.values():
        if not isinstance(page,dict) or set(page)!=keys:raise ValueError('exact_activity_fields_required')
        oid=page.pop('evidence_id');doc,detail=observation(oid)
        if doc['request']['stage'] not in ('detail','detail_readback') or doc['request'].get('title')!=page['title']:raise ValueError('exact_official_detail_required')
        if page['shop_id']!=policy['shop'] or page['url'] not in home_text:raise ValueError('official_home_link_or_shop_mismatch')
        if any(str(page[k]) not in detail for k in ('campaign_id','phase_id','title')):raise ValueError('activity_identity_not_in_captured_detail')
        page['page_evidence']=dict(sha256=oid,path=str(state/'observations'/(oid+'.json')))
        links.append(page['url'])
    assembled=dict(schema='campaign-intake-facts-v1',pages=pages,daily_campaign=facts['daily_campaign'],coverage=facts['coverage'],
        known_campaigns_complete=True,observed_links=links,discovery_evidence=dict(sha256=facts['discovery_id'],path=str(state/'observations'/(facts['discovery_id']+'.json'))),
        fixed_signup_template='D:/AI/畔色ERP系统/outputs/campaign-continuous/master-replacement-20260911/master.json')
    result=operator.save_prepared(assembled)  # Original identity/calendar/price-source validators.
    rid=result['request_id'];source=Path(result['request_path']);raw=source.read_bytes()
    if digest(json.loads(raw))!=rid:raise ValueError('prepared_identity_mismatch')
    save_immutable(state/'registered'/(rid+'.json'),json.dumps(dict(file_sha256=hashlib.sha256(raw).hexdigest()),sort_keys=True).encode())
    return dict(result,authorization_source='original_validator_and_broker_captured_evidence',maintenance_policy_changed=False)

def peer_sid(handle):
    import win32pipe,win32security,win32api,win32con
    win32security.ImpersonateNamedPipeClient(handle)
    try:
        token=win32security.OpenThreadToken(win32api.GetCurrentThread(),win32con.TOKEN_QUERY,True)
        try:return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
        finally:token.Close()
    finally:win32security.RevertToSelf()

def pipe_server(broker,*,pipe_name=PIPE,once=False,ready=None,connections=None,idle_timeout_ms=60000):
    import win32pipe,win32file,win32event,win32security,pywintypes
    sid=broker.policy['executor_sid']
    if not exact(sid,r'S-1-[0-9-]+'):raise ValueError('invalid_executor_sid')
    # No FILE_CREATE_PIPE_INSTANCE permission for the client; remote clients refused.
    sd=win32security.ConvertStringSecurityDescriptorToSecurityDescriptor('D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x12019b;;;'+sid+')',1)
    sa=pywintypes.SECURITY_ATTRIBUTES();sa.SECURITY_DESCRIPTOR=sd
    def wait(handle,ov,timeout):
        if win32event.WaitForSingleObject(ov.hEvent,timeout)!=win32event.WAIT_OBJECT_0:
            # All pipe IO is issued by this one server thread. The installed
            # pywin32 exposes CancelIo, not CancelIoEx. Reap the cancelled IO
            # before releasing its OVERLAPPED or buffer.
            win32file.CancelIo(handle)
            try:win32file.GetOverlappedResult(handle,ov,True)
            except pywintypes.error as exc:
                if exc.winerror!=995:raise
            raise TimeoutError('pipe_io_timeout')
        return win32file.GetOverlappedResult(handle,ov,False)
    handle=win32pipe.CreateNamedPipe(pipe_name,win32pipe.PIPE_ACCESS_DUPLEX|0x40000000|0x00080000,
        win32pipe.PIPE_TYPE_MESSAGE|win32pipe.PIPE_READMODE_MESSAGE|0x8,1,MAX_BYTES*8,MAX_BYTES+1,5000,sa)
    completed=0
    try:
        while True:
            events=[]
            def overlap():
                ov=pywintypes.OVERLAPPED();ov.hEvent=win32event.CreateEvent(None,True,False,None);events.append(ov.hEvent);return ov
            try:
                ov=overlap()
                try:win32pipe.ConnectNamedPipe(handle,ov)
                except pywintypes.error as exc:
                    if exc.winerror==535:win32event.SetEvent(ov.hEvent)
                    elif exc.winerror!=997:raise
                if ready:ready.set()
                wait(handle,ov,15000 if once else idle_timeout_ms)
                ov=overlap();_,buf=win32file.ReadFile(handle,MAX_BYTES+1,ov);size=wait(handle,ov,5000)
                if size>MAX_BYTES:raise ValueError('request_too_large')
                peer=peer_sid(handle)
                try:result=broker.handle(json.loads(bytes(buf[:size])),peer)
                except (ValueError,KeyError,TypeError,json.JSONDecodeError):result={'state':'rejected','reason':'request_not_permitted'}
                encoded=json.dumps(result,ensure_ascii=False).encode()
                ov=overlap();win32file.WriteFile(handle,encoded,ov);wait(handle,ov,5000)
                # Do not disconnect and discard an unread reply; client acknowledges
                # receipt. A missing acknowledgement has a bounded wait, no resend.
                try:
                    ov=overlap();_,ack_buffer=win32file.ReadFile(handle,1,ov);wait(handle,ov,5000)
                except (TimeoutError,pywintypes.error):pass
            except (TimeoutError,pywintypes.error,ValueError):
                if once:raise
            finally:
                try:win32pipe.DisconnectNamedPipe(handle)
                except pywintypes.error:pass
                for event in events:event.Close()
            completed+=1
            if once or (connections is not None and completed>=connections):return
    finally:handle.Close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--policy',type=Path,required=True);args=parser.parse_args()
    policy=json.loads(args.policy.read_text('utf-8-sig'))
    if policy.get('schema')!='panse-campaign-broker-v1':raise ValueError('policy_schema')
    # Only maintenance starts this process. The executor receives no API token.
    broker=Broker(policy,args.policy.parent.parent/'private-state',production_dispatch)
    try:pipe_server(broker)
    finally:broker.close()

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        import traceback
        # Diagnostic locations only: no request body, token or exception text.
        failure=dict(error_type=type(exc).__name__,native_error=getattr(exc,'winerror',None),
            locations=[dict(file=Path(f.filename).name,line=f.lineno) for f in traceback.extract_tb(exc.__traceback__)])
        try:(Path(__file__).resolve().parent.parent/'private-state/broker-last-error.json').write_text(json.dumps(failure),'utf-8')
        except OSError:pass
        raise

"""One unchanged-price resubmission after newer official constraints changed.

This continuation is not a new pricing rule. It preserves old failed/unknown
claims and the no-effect amendment guard, never changes a discount, and cannot
include the coworker-owned cabinet. Browser work stays in fixed recorded jobs.
"""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

from campaign_continuous_policy import RULE_SHA, fingerprint
from campaign_continuous_transport import CampaignTransport, persist
from campaign_entry_authority import load, file_sha

ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-continuous')
WA=Path('D:/AI/畔色ERP系统/Web-Agent程序/data/output/campaign-transfers')
ITEM='1007407909979'
PARENT='2c8df6bc7fb7e73bbf8425ef135806d21454c676e0cac437cf9fd5af32ada110'
CAMPAIGN='legacy/itemApply/3172207691'
BUNDLE='8011b70a71f72560535da94b3c8a5212283c1b23cdacba4aa717bfeb85a2e5d0'
READ_JOB='55ce06e770f5a878e6023f96a4065b5457e2210bc9b4f1edeb3d32ce68a0db24'
EXPORT=WA/'c31c668689a058c29e14767808b364c67027d86c9510b975c7a0f0e78cdb745a/已报商品-SKU收尾核对.xlsx'
EXPORT_SHA='51d28d02f7f79567bebc30db17a0db38326bab82cde12145cc77bd30bd6537b9'
SCOPE=Path('D:/AI/畔色ERP系统/outputs/campaign-remainder-20260914/product-scope.json')
REQUEST={'schema':'continuous_verified_failure_resubmit_v1','rule_sha':RULE_SHA,
         'parent_request_id':PARENT,'items':[ITEM],'price_change':False,'discount_change':False,
         'prewrite_predecessor':'60263186196c81016114a1acf5d61aa6933ccbe084af1773ba8c9887e44dcc87'}


def validate_request(request):
    if request!=REQUEST:raise ValueError('exact_unchanged_gilt_resubmit_required')
    return '畔色木作'


def official_rows(raw):
    from campaign_official_template import _archive,_read,_effective,sheet_path
    with _archive(raw) as archive:
        _,_,rows,merges=_read(archive,sheet_path(archive,'已报商品列表'))
        headers=[(n,c) for n,c in rows.items() if {'商品ID','营销ID','SKUID','商品状态'}.issubset(c.values())]
        if len(headers)!=1:raise ValueError('resubmit_export_schema_changed')
        n,header=headers[0];out=[]
        for number in sorted(rows):
            if number<=n:continue
            row={label:_effective(rows,merges,number,col).strip() for col,label in header.items()}
            if row.get('商品ID')==ITEM and row.get('商品状态')!='撤销报名':out.append(row)
        return out


def check_constraints(rows,signup):
    expected={r['sku']:r for r in signup}
    if (len(rows)!=len(expected) or {r.get('SKUID') for r in rows}!=set(expected)
            or {r.get('营销ID') for r in rows}!={'10030567401931'}
            or any(r.get('商品状态')!='异常' for r in rows)):
        raise ValueError('resubmit_current_failed_scope_changed')
    for row in rows:
        old=expected[row['SKUID']]
        numbers=[Decimal(row[k]) for k in ('活动价','最低标价','活动普惠券后价','最低普惠券后价要求')]
        if not all(n.is_finite() and n>0 for n in numbers):raise ValueError('resubmit_price_evidence_invalid')
        daily,cap,actual,ceiling=numbers
        if daily!=Decimal(old['activity_price']) or daily>cap or actual>ceiling:
            raise ValueError('resubmit_constraints_not_resolved')
        if not old['custom'] and (abs(actual-Decimal(old['target']))>2 or actual<Decimal(old['big_target'])):
            raise ValueError('resubmit_outside_existing_price_policy')
    return expected


def active_scope(scope):
    """Use the completed fixed DOM read plus exact full export, not stock."""
    from campaign_recorded_sku_state import row_matches
    path=WA/READ_JOB/'sku-batch-read.json';batch=load(path)
    db=sqlite3.connect((WA/'jobs.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    try:r=db.execute('SELECT operation,state,result,request FROM campaign_transfer_jobs WHERE id=?',(READ_JOB,)).fetchone()
    finally:db.close()
    if not r or r[:2]!=('product_final_audit_read','finished'):raise ValueError('resubmit_fixed_read_not_finished')
    observed,request=json.loads(r[2]),json.loads(r[3]);payload=request['payload']
    if (fingerprint(['product_final_audit_read',payload])!=READ_JOB or fingerprint(payload)!=request['request_sha']
            or payload['parent_request_id']!=PARENT or batch['source']!=payload
            or {k:v for k,v in observed.items() if k!='evidence_path'}!=batch
            or Path(observed['evidence_path']).resolve()!=path.resolve()
            or batch['platform_write'] is not False or batch['unread_items'] or batch['error']):
        raise ValueError('resubmit_fixed_read_identity_changed')
    video=batch['recording']
    if not video.get('frames') or file_sha(video['video'])!=video['video_sha256']:
        raise ValueError('resubmit_fixed_read_video_changed')
    records=[r for r in batch['records'] if r['item']==ITEM and r['state']=='read' and r['platform_write'] is False]
    if len(records)!=1 or scope['complete'] is not True:raise ValueError('resubmit_exact_product_read_required')
    facts=[e['facts'] for e in scope['sku_facts'] if e['facts']['item']==ITEM];active=[]
    for fact in facts:
        matches=[r for r in records[0]['rows'] if row_matches(fact,r)]
        if len(matches)!=1 or fact['sku'] not in records[0]['requested_skus']:
            raise ValueError('resubmit_switch_export_identity_not_unique')
        if switch_state(matches[0]):active.append(fact['sku'])
    return set(active)


def switch_state(row):
    switches=row.get('switches',[])
    if len(switches)!=1:raise ValueError('resubmit_explicit_switch_required')
    switch=switches[0];classes=set(switch.get('class_name','').split());aria=switch.get('aria')
    on='next-switch-on' in classes;off='next-switch-off' in classes
    if on==off:raise ValueError('resubmit_unknown_switch')
    if on and aria in ('true','1') and row.get('enabled') in (None,True):return True
    if off and aria in ('false','0') and row.get('enabled') in (None,False):return False
    raise ValueError('resubmit_conflicting_switch_evidence')


def prepare(request,authority,root):
    from campaign_generate_current_files import _generate
    from campaign_segmented_time import build_plan,bind_request
    validate_request(request);root=Path(root)
    verify_prewrite_predecessor(authority)
    previous=load(ROOT/'requests'/(PARENT+'.json'))
    if fingerprint(previous)!=PARENT or load(ROOT/'runs'/PARENT/'result.json')['status']!='complete':
        raise ValueError('resubmit_parent_not_settled')
    page=previous['pages'][CAMPAIGN]
    if ITEM in authority.blocked(CAMPAIGN,'signup',page['start'],page['end']):
        raise ValueError('resubmit_success_or_unknown_protected')
    latest=authority.db.execute("SELECT status,evidence FROM attempts WHERE item=? AND campaign=? AND phase='signup' ORDER BY rowid DESC LIMIT 1",(ITEM,CAMPAIGN)).fetchone()
    if not latest or latest['status']!='failed' or str(json.loads(latest['evidence'])['batch'])!='843589003':
        raise ValueError('resubmit_new_terminal_requires_its_own_report')
    old=authority.get_bundle(BUNDLE)
    if file_sha(EXPORT)!=EXPORT_SHA:raise ValueError('resubmit_official_export_changed')
    expected=check_constraints(official_rows(EXPORT.read_bytes()),old['signup_rows'])
    scope=load(SCOPE)
    # Validate every page of the already downloaded export; never download again.
    from campaign_product_scope import from_edge_job
    observation=load(SCOPE.parent/'product-export-observation.json')
    verified=from_edge_job(observation,expected_request_id='2f24afabf910c86f9be065882c2a108b3bc4cbe3bc0c6b9e65d4527eaf4de9a3',expected_shop='畔色木作',roots=[WA])
    if verified!=scope or active_scope(scope)!=set(expected):raise ValueError('resubmit_full_active_scope_changed')
    snapshot=authority.resolve_snapshot(load(old['snapshot_path']))
    snapshot_path=Path(persist(root/'snapshot.json',snapshot))
    timing=load(old['time_binding']['request_path']);timing['price_version']=snapshot['resolved_price_version_sha256']
    time_path=persist(root/'time-request.json',timing)
    plan=build_plan(timing)
    segments=[s for s in plan['segments'] if s['campaign']==CAMPAIGN and s['price_window']=={'start':old['start'],'end':old['end']}]
    if len(segments)!=1:raise ValueError('resubmit_exact_window_missing')
    binding=bind_request(time_path,segments[0]['segment_id'])
    args=SimpleNamespace(snapshot=snapshot_path,activity_template=Path(old['template_path']),campaign_key=CAMPAIGN,
        official_rate=old['official_rate'],target=old['target'],start=old['start'],end=old['end'],custom_basis_receipt=[],
        signup_items=ITEM,discount_items=ITEM,continuous_rule_sha=RULE_SHA,output_dir=root/'files',custom_corrections=None,
        sku_exclusion_receipts=old.get('sku_exclusion_receipts',[]),time_request=Path(time_path),time_segment=segments[0]['segment_id'])
    generated=_generate(args,authority)
    if generated['issues']:raise ValueError('resubmit_generation_inputs_invalid:'+generated['issues'][0]['error'])
    body=authority.get_bundle(generated['entry_bundle_id'])
    if body['discount_rows'] or {r['sku']:r['activity_price'] for r in body['signup_rows']}!={s:r['activity_price'] for s,r in expected.items()}:
        raise ValueError('resubmit_would_change_price_or_discount')
    value=dict(bundle_id=generated['entry_bundle_id'],items=[ITEM],identity=page,time_binding=binding,
        source_scope={'path':str(SCOPE),'sha256':file_sha(SCOPE)},active_skus=sorted(expected),
        official_constraint_evidence={'path':str(EXPORT),'sha256':EXPORT_SHA},price_change=False,discount_change=False)
    persist(root/'prepared.json',value)
    return value


def verify_prewrite_predecessor(authority):
    """The old local source-path failure occurred before creating any job/claim."""
    old=ROOT/'runs'/REQUEST['prewrite_predecessor']
    prepared=load(old/'prepared.json')
    log=(old/'process.log').read_text(encoding='utf-8')
    if ('body=verify_prepared(p,authority)' not in log
            or not log.rstrip().endswith('ValueError: mapping_or_price_authority_changed_regenerate_local_files')
            or (old/'execution').exists() or (old/'signup-terminal.json').exists()
            or authority.db.execute('SELECT 1 FROM attempts WHERE bundle_id=? LIMIT 1',(prepared['bundle_id'],)).fetchone()):
        raise ValueError('resubmit_predecessor_not_proven_before_actions')
    return {'request_id':REQUEST['prewrite_predecessor'],'log_sha256':file_sha(old/'process.log'),
            'old_claim_released':False,'old_request_restarted':False}


def settle_prewrite_predecessor(authority):
    """Close a proven local rejection, never an unknown external write."""
    proof=verify_prewrite_predecessor(authority);rid=proof['request_id']
    db=sqlite3.connect(ROOT/'jobs.sqlite3',isolation_level=None)
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT state,result FROM continuous_jobs WHERE id=?',(rid,)).fetchone()
        if not row or row[0]!='unknown' or json.loads(row[1]).get('reason')!='controller_did_not_produce_terminal_receipt':
            raise ValueError('prewrite_controller_not_original_local_rejection')
        result=dict(status='complete',all_signed_up=False,platform_write=False,
            reason='local_preparation_rejected_before_any_external_action',prior_state=row[0],
            prior_result=json.loads(row[1]),evidence=proof,automatic_retry=False,
            completion_scope='local_attempt_only_not_campaign_completion')
        persist(ROOT/'runs'/rid/'prewrite-local-rejection.json',result)
        db.execute("UPDATE continuous_jobs SET state='finished',result=? WHERE id=? AND state='unknown'",(json.dumps(result,ensure_ascii=False),rid))
        db.execute('COMMIT');return result
    except BaseException:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()


def audit_manifest(root,segment):
    path=Path(root)/'prepared.json';p=load(path)
    if (segment.get('completion_kind')!=REQUEST['schema'] or p['items']!=[ITEM]
            or file_sha(p['source_scope']['path'])!=p['source_scope']['sha256']):
        raise ValueError('resubmit_audit_scope_changed')
    body_skus=p['active_skus']
    if active_scope(load(p['source_scope']['path']))!=set(body_skus):raise ValueError('resubmit_audit_active_scope_changed')
    return dict(payload={'identity':p['identity'],'time_binding':p['time_binding']},
        window=p['time_binding']['segment']['price_window'],pairs=[dict(item=ITEM,sku=s) for s in body_skus],
        scope_sha256=file_sha(path),scope_path=str(path))


def verify_prepared(p,authority):
    from campaign_submission_gate import validated_body
    if (p.get('items')!=[ITEM] or p.get('price_change') is not False or p.get('discount_change') is not False
            or p.get('official_constraint_evidence')!={'path':str(EXPORT),'sha256':EXPORT_SHA}
            or file_sha(EXPORT)!=EXPORT_SHA):raise ValueError('resubmit_prepared_scope_changed')
    body,_=validated_body(authority,p['bundle_id'],'signup')
    old=authority.get_bundle(BUNDLE)
    if (body['campaign']!=CAMPAIGN or body['discount_rows'] or body['time_binding']!=p['time_binding']
            or (body['start'],body['end'])!=(old['start'],old['end'])
            or {r['sku']:r['activity_price'] for r in body['signup_rows']}!=
               {r['sku']:r['activity_price'] for r in old['signup_rows']}
            or p['identity']!=load(ROOT/'requests'/(PARENT+'.json'))['pages'][CAMPAIGN]
            or set(p['active_skus'])!={r['sku'] for r in body['signup_rows']}):
        raise ValueError('resubmit_prepared_price_or_identity_changed')
    check_constraints(official_rows(EXPORT.read_bytes()),body['signup_rows'])
    if (p['source_scope']!={'path':str(SCOPE),'sha256':file_sha(SCOPE)}
            or active_scope(load(SCOPE))!=set(p['active_skus'])):raise ValueError('resubmit_active_scope_changed')
    return body


def execute(request,*,root,authority,edge,artifact_roots):
    validate_request(request);root=Path(root);persist(root/'request.json',request)
    p=load(root/'prepared.json') if (root/'prepared.json').exists() else prepare(request,authority,root)
    body=verify_prepared(p,authority)
    t=CampaignTransport(edge,authority,root=root/'execution',request={},artifact_roots=artifact_roots)
    persist(t.root/'resolved-snapshot.json',load(body['snapshot_path']))
    payload={'identity':p['identity'],'items':[ITEM],'bundle':{'bundle_id':p['bundle_id']},'time_binding':p['time_binding']}
    terminal_path=root/'signup-terminal.json'
    if terminal_path.exists():terminal=load(terminal_path)
    else:
        if not (root/'execution'/'signup'/'claim.json').exists():
            latest=authority.db.execute("SELECT status,evidence FROM attempts WHERE item=? AND campaign=? AND phase='signup' ORDER BY rowid DESC LIMIT 1",(ITEM,CAMPAIGN)).fetchone()
            if not latest or latest['status']!='failed' or str(json.loads(latest['evidence'])['batch'])!='843589003':
                raise ValueError('resubmit_new_terminal_requires_its_own_report')
        # Same-window successful discounts are reused. This is the original
        # mandatory time/amount readback, not a price change or a new preflight.
        verified=root/'discount-window-verified.json'
        if not verified.exists():
            checked=t.step_verify_discount_window(fingerprint([request,'reuse-window']),payload,root/'execution'/'reuse-window')
            if checked.get('all_correct') is not True:raise ValueError('resubmit_discount_window_not_verified')
            persist(verified,checked)
        terminal=t.submit_phase('signup',payload,root/'execution'/'signup')
        persist(terminal_path,terminal)
    failures=[r for r in terminal['items'] if r['outcome']=='failed']
    errors={}
    if failures:
        report=t.execute('report',fingerprint(['unchanged-report',terminal['batch']]),
            dict(identity=p['identity'],rule_sha=RULE_SHA,items=[ITEM],batch=terminal['batch'],failed_phase='signup'))
        errors={ITEM:report.get('errors',[])}
    result={'status':'complete','all_signed_up':False,'parent_request_id':PARENT,
        'segments':[dict(segment_id=p['time_binding']['segment']['segment_id'],completion_kind=REQUEST['schema'],
            initial_scope=[ITEM],success={} if failures else {ITEM:terminal['batch']},exceptions=errors,pending=[])],
        'price_change':False,'discount_change':False,'automatic_rotation':False,'coworker_item_touched':False,
        'signup_terminal':terminal}
    from campaign_final_audit import finalize
    return finalize(request,result,root=root,authority=authority,edge=edge,artifact_roots=artifact_roots)


def check_read_validation_recovery(output,authority):
    output=Path(output);p=load(output/'prepared.json')
    verify_prepared(p,authority)
    log=(output/'process.log').read_text(encoding='utf-8')
    if ("checked=t.step_verify_discount_window('reuse-window',payload" not in log
            or not log.rstrip().endswith('campaign_edge_client.EdgeJobError: SurfaceError')
            or list(output.rglob('*-job.json')) or list(output.rglob('claim.json'))
            or (output/'signup-terminal.json').exists()
            or authority.db.execute('SELECT 1 FROM attempts WHERE bundle_id=? LIMIT 1',(p['bundle_id'],)).fetchone()):
        raise ValueError('read_validation_recovery_requires_proven_no_jobs_or_claims')
    return {'log_sha256':file_sha(output/'process.log'),'no_browser_job_created':True,
            'no_claim_created':True,'write_claims_released':False}


async def recover_read_validation():
    """Maintenance recovery CLI: same persistent worker/request, once only."""
    from campaign_entry_authority import Authority
    import sys
    sys.path.insert(0,str(WA.parents[2]))
    from app.engine.campaign_continuous_worker import ContinuousWorker
    rid=fingerprint(REQUEST);output=ROOT/'runs'/rid
    a=Authority();worker=ContinuousWorker()
    try:
        proof=check_read_validation_recovery(output,a)
        worker.db.execute('BEGIN IMMEDIATE')
        old=worker.status(rid)
        if old['state']!='unknown' or worker.db.execute("SELECT 1 FROM continuous_jobs WHERE state='running'").fetchone():
            raise ValueError('read_validation_recovery_requires_idle_original_unknown')
        marker=output/'read-validation-recovery.json'
        if marker.exists():raise ValueError('read_validation_recovery_already_consumed')
        persist(marker,dict(proof,request_id=rid,previous_controller=old))
        worker.db.execute("UPDATE continuous_jobs SET state='running',result=NULL WHERE id=?",(rid,))
        worker.db.execute('COMMIT')
        await worker.execute(rid,log_name='process-read-validation-recovery.log')
        return worker.status(rid)
    finally:
        if worker.db.in_transaction:worker.db.execute('ROLLBACK')
        worker.db.close();a.close()


async def recover_signup_binding():
    """Resume the same unconsumed signup job, then finish its controller."""
    from campaign_entry_authority import Authority
    from campaign_edge_client import EdgeClient
    from campaign_edge_receipt import reconcile_signup
    import sys
    sys.path.insert(0,str(WA.parents[2]))
    from app.vault.store import get_or_create_api_token
    from app.engine.campaign_continuous_worker import ContinuousWorker
    rid=fingerprint(REQUEST);root=ROOT/'runs'/rid
    a=Authority();worker=ContinuousWorker();edge=EdgeClient(get_or_create_api_token())
    try:
        p=load(root/'prepared.json');verify_prepared(p,a)
        ref=load(root/'execution/signup/signup-job.json');jid=ref['job_id']
        if jid!='35175825acb6b72ce9da579d908708baa07a5decfb40e59c44b800ea62c151ce':
            raise ValueError('resubmit_exact_original_binding_job_required')
        marker=root/'signup-binding-program-recovery.json'
        if marker.exists():raise ValueError('resubmit_binding_recovery_already_consumed')
        old=worker.status(rid)
        if old['state']!='unknown' or worker.db.execute("SELECT 1 FROM continuous_jobs WHERE state='running'").fetchone():
            raise ValueError('resubmit_binding_controller_not_idle')
        job=edge.status(jid)
        if job['state']!='unknown' or job.get('result',{}).get('reason')!='fixed_campaign_entry_not_unique':
            raise ValueError('resubmit_binding_failure_not_exact')
        from campaign_submission_gate import verify_claim
        claim=load(root/'execution/signup/claim.json')['claim_id']
        verify_claim(a,claim)
        persist(marker,dict(previous_controller=old,previous_job=job,claim_id=claim,claim_released=False))
        edge._action('program_resume_signup_before_page_binding',{'job_id':jid})
        job=edge.wait(jid)
        folder=root/'binding-recovery-result'
        persist(folder/'signup-observation.json',job)
        if job['state']!='finished':return dict(state='unknown',job_id=jid,result=job.get('result'),automatic_retry=False)
        terminal=reconcile_signup(a,job,output_dir=folder)
        persist(root/'signup-terminal.json',terminal)
        persist(root/'execution/terminals'/('signup-'+str(terminal['batch'])+'.json'),dict(terminal,bundle_id=p['bundle_id']))
        worker.db.execute("UPDATE continuous_jobs SET state='running',result=NULL WHERE id=? AND state='unknown'",(rid,))
        await worker.execute(rid,log_name='process-signup-binding-recovery.log')
        return worker.status(rid)
    finally:worker.db.close();a.close()


if __name__=='__main__':
    import argparse,asyncio
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--recover-read-validation',action='store_true')
    mode.add_argument('--recover-signup-binding',action='store_true')
    args=parser.parse_args()
    print(json.dumps(asyncio.run(recover_signup_binding() if args.recover_signup_binding else recover_read_validation()),ensure_ascii=False))

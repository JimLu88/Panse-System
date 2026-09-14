"""Complete only Puta's existing replacement in the approved Super window.

No new rotation, no Autumn retry, no coworker cabinet. The fixed program owns
offer discovery, exact new-row inclusion and the normal failed-scope loop.
"""
from copy import deepcopy
from pathlib import Path
import importlib.util
import json

from campaign_continuous_policy import RULE_SHA,fingerprint
from campaign_continuous_transport import CampaignTransport,persist
from campaign_entry_authority import load,file_sha
import campaign_verified_failure_resubmit as proof

ITEM='722275846168'
NEW='6301271146899'
OFFER='145761399121'
PARENT=proof.PARENT
SEGMENT='bd2877766c31b1fa89ac5c095e63fbbb5a9123f298ec041e0aab8f24fe5d0fd7'
WINDOW={'start':'2026-09-28 00:00:00','end':'2026-09-30 23:59:59'}
CAMPAIGN=proof.CAMPAIGN
REQUEST={'schema':'continuous_puta_existing_new_sku_v1','rule_sha':RULE_SHA,
         'parent_request_id':PARENT,'items':[ITEM],'existing_new_sku':NEW,'price_window':WINDOW}


def validate_request(request):
    if request!=REQUEST:raise ValueError('exact_puta_super_new_sku_scope_required')
    return '畔色木作'


def prepare(request,authority,root):
    from campaign_recorded_sku_state import row_matches
    from campaign_product_scope import from_edge_job
    from campaign_segmented_time import build_plan,bind_request
    validate_request(request);root=Path(root)
    original=load(proof.ROOT/'requests'/(PARENT+'.json'))
    if fingerprint(original)!=PARENT:raise ValueError('puta_original_parent_changed')
    page=original['pages'][CAMPAIGN]
    latest=authority.db.execute("SELECT id,status FROM attempts WHERE item=? AND campaign=? AND phase='signup' ORDER BY rowid DESC LIMIT 1",(ITEM,CAMPAIGN)).fetchone()
    if (not latest or tuple(latest)!=('ce7be145a079482badeee181010b3792:'+ITEM,'failed')
            or ITEM in authority.blocked(CAMPAIGN,'signup',page['start'],page['end'])):
        raise ValueError('puta_exact_previous_failed_attempt_required')
    scope=load(proof.SCOPE)
    observation=load(proof.SCOPE.parent/'product-export-observation.json')
    verified=from_edge_job(observation,expected_request_id='2f24afabf910c86f9be065882c2a108b3bc4cbe3bc0c6b9e65d4527eaf4de9a3',expected_shop='畔色木作',roots=[proof.WA])
    if verified!=scope:raise ValueError('puta_complete_export_changed')
    # Revalidate the entire fixed read's job, request, file and video before
    # selecting this product's rows from that same recorded batch.
    proof.active_scope(scope)
    batch=load(proof.WA/proof.READ_JOB/'sku-batch-read.json')
    records=[r for r in batch['records'] if r['item']==ITEM and r['state']=='read' and r['platform_write'] is False]
    if len(records)!=1:raise ValueError('puta_explicit_switch_read_required')
    facts=[e['facts'] for e in scope['sku_facts'] if e['facts']['item']==ITEM];active=[];off=[]
    for fact in facts:
        rows=[r for r in records[0]['rows'] if row_matches(fact,r)]
        if fact['sku']==NEW and (len(rows)!=1 or proof.switch_state(rows[0]) is not True):
            raise ValueError('puta_new_sku_not_verified_enabled')
        # Only exact recorded OFF facts exclude rows. A stock change, absent
        # requested-ID hint or uncertain state must not drop another SKU.
        if len(rows)==1 and fact['sku'] in records[0]['requested_skus'] and proof.switch_state(rows[0]) is False:
            off.append(fact['sku'])
        else:active.append(fact['sku'])
    if set(off)!={'5193229462947','5193229462948'} or set(active)!={NEW,'6299575587331','5193229462949','5193229462950','5193229462951','5193229462952'}:
        raise ValueError('puta_replacement_scope_changed')
    scoped=deepcopy(scope)
    scoped['sku_facts']=[e for e in scope['sku_facts'] if not (e['facts']['item']==ITEM and e['facts']['sku'] in off)]
    scoped['file_only_disabled_exclusions']={'item':ITEM,'skus':off,'scope_path':str(proof.SCOPE),
        'scope_sha256':file_sha(proof.SCOPE),'read_path':str(proof.WA/proof.READ_JOB/'sku-batch-read.json'),
        'read_sha256':file_sha(proof.WA/proof.READ_JOB/'sku-batch-read.json'),'stock_used_as_state':False}
    snapshot=authority.resolve_snapshot(load(proof.ROOT/'runs'/PARENT/'segments'/SEGMENT/'resolved-snapshot.json'))
    snapshot_path=persist(root/'execution'/'resolved-snapshot.json',snapshot)
    persist(root/'execution'/'product-scope.json',scoped)
    timing=load(proof.ROOT/'runs'/PARENT/'time-request.json');timing['price_version']=snapshot['resolved_price_version_sha256']
    timing_path=persist(root/'time-request.json',timing)
    segments=[s for s in build_plan(timing)['segments'] if s['campaign']==CAMPAIGN and s['price_window']==WINDOW]
    if len(segments)!=1:raise ValueError('puta_exact_timing_required')
    p=dict(identity=page,snapshot={'path':snapshot_path,'sha256':file_sha(snapshot_path)},price_version=snapshot['resolved_price_version_sha256'],
        time_binding=bind_request(timing_path,segments[0]['segment_id']),fixed_signup_template=original['fixed_signup_template'],
        active_skus=sorted(active),excluded_off_skus=sorted(off),original_failed_attempt=latest['id'])
    persist(root/'prepared.json',p);return p


class PutaTransport(CampaignTransport):
    def step_scope(self,action_id,payload,folder):
        scope=load(self.root/'product-scope.json');snapshot=load(self.root/'resolved-snapshot.json')
        return dict(scope,erp_sellable=[ITEM],price_version=snapshot['resolved_price_version_sha256'],
            prior_outcomes={},changed_existing_sku_scope=True,
            previous_failed_attempt='ce7be145a079482badeee181010b3792:'+ITEM)

    def execute(self,step,action_id,payload):
        if payload.get('items') and payload['items']!=[ITEM]:raise ValueError('puta_continuation_scope_expanded')
        if step=='discount':raise ValueError('puta_never_replay_original_partial_discount')
        return super().execute(step,action_id,payload)

    def recover_prior_failures(self,*args):return {}
    def recover_prior_price_gaps(self,*args):return {}
    def recover_missing_inputs(self,*args):return {}


def execute(request,*,root,authority,edge,artifact_roots):
    from campaign_discount_include import prepare as include_prepare,record
    from campaign_continuous_flow import Store,run
    validate_request(request);root=Path(root);persist(root/'request.json',request)
    p=load(root/'prepared.json') if (root/'prepared.json').exists() else prepare(request,authority,root)
    if (p['identity']!=load(proof.ROOT/'requests'/(PARENT+'.json'))['pages'][CAMPAIGN]
            or p['time_binding']['segment']['price_window']!=WINDOW):raise ValueError('puta_prepared_identity_changed')
    t=PutaTransport(edge,authority,root=root/'execution',request={'target':'medium',
        'time_request':p['time_binding']['request_path'],'fixed_signup_template':p['fixed_signup_template']},artifact_roots=artifact_roots)
    identity=t.identity({'identity':p['identity']})
    proofroot=root/'inclusion';done=proofroot/'verified.json'
    if not done.exists():
        discovery=t.job('discount_item_discovery',dict(identity=identity,items=[ITEM],read_request_id=fingerprint([request,'offer-coverage'])),proofroot/'coverage')
        spec=importlib.util.spec_from_file_location('recorded_offer_coverage',proof.WA.parents[2]/'app/engine/campaign_discount_item_coverage.py')
        parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
        coverage=parser.analyze([r['evidence_path'] for r in discovery['result']['rows']],WINDOW['start'],WINDOW['end'])
        coverage_path=persist(proofroot/'coverage.json',coverage)
        missing=t.job('discount_readback',dict(identity=identity,read_request_id=fingerprint([request,'missing-new-sku']),
            price_window=WINDOW,offers=[dict(offer_id=OFFER,item=ITEM,sku_ids=[NEW])]),proofroot/'absence')
        read_path=proofroot/'absence'/'discount_readback-observation.json'
        inclusion={'schema':'campaign_missing_discount_include_v1','campaign':CAMPAIGN,'shop':'畔色木作',
            **WINDOW,'rate':'0.10','offer_id':OFFER,'price_version':p['price_version'],'snapshot':p['snapshot'],
            'coverage':{'path':coverage_path,'sha256':file_sha(coverage_path)},
            'missing_read':{'path':str(read_path),'sha256':file_sha(read_path)}}
        ref=proofroot/'claim.json'
        claim=load(ref) if ref.exists() else include_prepare(authority,inclusion)
        persist(ref,claim)
        if len(claim['rows'])!=1 or (claim['rows'][0]['item'],claim['rows'][0]['sku'])!=(ITEM,NEW):
            raise ValueError('puta_include_not_exact_new_row')
        t.job('discount_include',dict(identity=identity,claim_id=claim['claim_id']),proofroot/'save')
        receipt=record(authority,claim['claim_id'],proofroot/'save'/'discount_include-observation.json')
        persist(done,receipt)
    store=Store(root/'controller.sqlite3')
    try:
        original=load(proof.ROOT/'requests'/(PARENT+'.json'))
        value=run(store,t,p['identity'],expected_shop='畔色木作',observed_links=original['observed_links'],time_binding=p['time_binding'])
    finally:store.close()
    result=dict(status=value['status'],all_signed_up=False,parent_request_id=PARENT,
        segments=[dict(value,segment_id=p['time_binding']['segment']['segment_id'])],
        original_partial_claim_preserved=True,rotation_performed=False,coworker_item_touched=False,autumn_retried=False)
    from campaign_final_audit import finalize
    return finalize(request,result,root=root,authority=authority,edge=edge,artifact_roots=artifact_roots)


async def recover_include_window():
    """Resume the original unconsumed include and then its whole controller."""
    import sys
    sys.path.insert(0,str(proof.WA.parents[2]))
    from app.vault.store import get_or_create_api_token
    from app.engine.campaign_continuous_worker import ContinuousWorker
    from campaign_entry_authority import Authority
    from campaign_edge_client import EdgeClient
    from campaign_discount_include import verify,record
    rid=fingerprint(REQUEST);root=proof.ROOT/'runs'/rid
    a=Authority();worker=ContinuousWorker();edge=EdgeClient(get_or_create_api_token())
    try:
        marker=root/'include-window-program-recovery.json'
        if marker.exists():raise ValueError('include_window_recovery_already_consumed')
        old=worker.status(rid)
        if old['state']!='unknown' or worker.db.execute("SELECT 1 FROM continuous_jobs WHERE state='running'").fetchone():raise ValueError('original_puta_controller_not_idle')
        folder=root/'inclusion/save';ref=load(folder/'discount_include-job.json');jid=ref['job_id']
        if jid!='7045027ef0d5f12deb156d77de5521f22da40b3251647744df480333c8a0cfdf':raise ValueError('exact_original_include_job_required')
        claim=load(root/'inclusion/claim.json')['claim_id'];verify(a,claim)
        prior=edge.status(jid)
        if prior['state']!='unknown' or prior.get('result',{}).get('reason')!='include_claim_identity_mismatch':raise ValueError('original_include_failure_changed')
        persist(marker,dict(previous_controller=old,previous_job=prior,claim_id=claim,claim_released=False))
        edge._action('program_resume_include_before_window_binding',{'job_id':jid})
        job=edge.wait(jid)
        path=root/'inclusion/window-recovery/discount_include-observation.json';persist(path,job)
        if job['state']!='finished':return dict(state='unknown',job_id=jid,result=job.get('result'),automatic_retry=False)
        persist(root/'inclusion/verified.json',record(a,claim,path))
        worker.db.execute("UPDATE continuous_jobs SET state='running',result=NULL WHERE id=? AND state='unknown'",(rid,))
        await worker.execute(rid,log_name='process-include-window-recovery.log')
        return worker.status(rid)
    finally:worker.db.close();a.close()


if __name__=='__main__':
    import argparse,asyncio
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recover-include-window',required=True,action='store_true')
    parser.parse_args()
    print(json.dumps(asyncio.run(recover_include_window()),ensure_ascii=False))

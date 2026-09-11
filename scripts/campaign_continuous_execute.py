"""Installed continuous entry, invoked by Web-Agent with one immutable request.

AI supplies only official discovery identities/calendar. This program owns all
export/generation/upload/report/repair steps. Token arrives over private stdin,
never in arguments, request artifacts, output, or logs. No browser fallback.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from campaign_continuous_policy import RULE_SHA, activity_identity, fingerprint, load_rules
from campaign_continuous_transport import CampaignTransport, persist
from campaign_continuous_flow import Store, run
from campaign_edge_client import EdgeClient
from campaign_entry_authority import Authority, file_sha, load
from campaign_segmented_time import bind_request, build_plan

ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-continuous')


def validate_request(request):
    load_rules()
    if request.get('rule_sha')!=RULE_SHA or request.get('schema')!='continuous_campaign_request_v1':
        raise ValueError('approved_continuous_request_required')
    existing=request.get('existing_product_export')
    if existing is not None:
        import re
        if (not isinstance(existing,dict) or set(existing)!={'job_id','snapshot_request_id'}
                or any(not re.fullmatch('[0-9a-f]{64}',str(existing[k])) for k in existing)):
            raise ValueError('exact_existing_product_export_reference_required')
    calendar=request['calendar'];shop=calendar['shop_id'];pages=request['pages']
    expected={calendar['daily_activity']['campaign'],*[c['campaign'] for c in calendar['campaigns']]}
    if set(pages)!=expected:raise ValueError('calendar_discovery_page_scope_mismatch')
    for key,page in pages.items():
        activity_identity(page,expected_shop=shop,observed_links=request['observed_links'])
        if key!='/'.join(str(page[k]) for k in ('campaign_id','phase_id','sign_record_id')):
            raise ValueError('discovery_campaign_identity_mismatch')
        source=next(c for c in [calendar['daily_activity'],*calendar['campaigns']] if c['campaign']==key)
        if any(str(source[k])!=str(page[k]) for k in ('shop_id','start','end','official_rate','page_evidence')):
            raise ValueError('calendar_not_bound_to_observed_page')
    # Price version is resolved AFTER the one required product export/mapping.
    build_plan(dict(calendar,price_version='resolved_after_single_scope_export'))
    return shop


def register_request(request,root=ROOT):
    """AI discovery boundary: validate identities; no export, edit or upload."""
    shop=validate_request(request)
    rid=fingerprint(request)
    persist(Path(root)/'requests'/(rid+'.json'),request)
    return {'ok':True,'request_id':rid,'shop':shop,'state':'registered',
            'platform_write':False,'automatic_rotation':False}


def execute_request(request, *, root, authority, edge, artifact_roots, progress=None):
    shop=validate_request(request);root=Path(root)
    persist(root/'request.json',request)
    store=Store(root/'controller.sqlite3')
    try:
        first=request['pages'][request['calendar']['daily_activity']['campaign']]
        base={'identity':first,'rule_sha':RULE_SHA}
        transport=CampaignTransport(edge,authority,root=root/'shared',request=request,artifact_roots=artifact_roots,progress=progress)
        # Saved through the same once-only ledger as all later actions. A crash
        # during export cannot silently submit another export on restart.
        pre_id=store.start(fingerprint(['scope',request]),RULE_SHA)
        owner=store.lock(pre_id,shop)
        try:scope=store.once(pre_id,'scope',base,transport)
        finally:store.unlock(pre_id,owner)
        calendar=dict(request['calendar'],price_version=scope['price_version'])
        time_path=persist(root/'time-request.json',calendar)
        plan=build_plan(calendar);results=[]
        for segment in plan['segments']:
            page=request['pages'][segment['campaign']]
            options={'target':segment['target'],'time_request':time_path}
            if segment['kind']=='daily':
                options['fixed_signup_template']=request.get('fixed_signup_template')
                if not options['fixed_signup_template']:
                    results.append({'status':'blocked','step':'template','reason':'fixed_super_reduce_master_missing',
                                    'segment_id':segment['segment_id'],'all_signed_up':False})
                    continue
            local=CampaignTransport(edge,authority,root=root/'segments'/segment['segment_id'],
                request=options,artifact_roots=artifact_roots,progress=progress)
            local.shared_scope=(scope,transport.root)
            binding=bind_request(time_path,segment['segment_id'])
            value=run(store,local,page,expected_shop=shop,observed_links=request['observed_links'],time_binding=binding)
            results.append(dict(value,segment_id=segment['segment_id']))
            # Unknown external writes or security gates are never worked around
            # by another activity. Pure missing-input segments may be skipped.
            if value['status']=='blocked':break
        result={'status':'complete' if len(results)==len(plan['segments']) and all(r['status']=='complete' for r in results) else 'blocked',
                'all_signed_up':len(results)==len(plan['segments']) and all(r.get('all_signed_up') for r in results),
                'segments':results,'plan_id':plan['plan_id'],'legacy_fallback':False}
        recordings=[]
        for p in root.rglob('*-observation.json'):
            job=load(p);recording=(job.get('result') or {}).get('recording')
            if recording:recordings.append({'job_id':job['job_id'],'operation':job['operation'],'recording':recording})
        result['recordings']=recordings
        result['full_recording_verified']=bool(recordings) and all(
            r['recording'].get('video') and not r['recording'].get('video_error')
            and not r['recording'].get('error') and not r['recording'].get('capture_errors') for r in recordings)
        persist(root/'result.json',result)
        return result
    finally:store.db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--request-id')
    mode.add_argument('--register-discovery',action='store_true')
    args=p.parse_args()
    if args.register_discovery:
        print(json.dumps(register_request(json.load(sys.stdin)),ensure_ascii=False));return
    import re
    if not re.fullmatch('[0-9a-f]{64}',args.request_id):raise ValueError('invalid_request_id')
    path=ROOT/'requests'/(args.request_id+'.json');request=load(path)
    if fingerprint(request)!=args.request_id:raise ValueError('request_identity_changed')
    secret=json.loads(sys.stdin.readline());edge=EdgeClient(secret.pop('token'))
    a=Authority()
    try:
        result=execute_request(request,root=ROOT/'runs'/args.request_id,authority=a,edge=edge,
            artifact_roots=secret['artifact_roots'],
            progress=lambda job: print(json.dumps({'progress':job['state'],'job_id':job['job_id']},ensure_ascii=False),flush=True))
        print(json.dumps(result,ensure_ascii=False))
    finally:a.close()


if __name__=='__main__':main()

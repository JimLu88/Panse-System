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
    if request.get('schema')=='continuous_puta_existing_new_sku_v1':
        from campaign_puta_new_sku_completion import validate_request as validate_puta
        return validate_puta(request)
    if request.get('schema')=='continuous_verified_failure_resubmit_v1':
        from campaign_verified_failure_resubmit import validate_request as validate_resubmit
        return validate_resubmit(request)
    if request.get('schema')=='continuous_campaign_autumn_cost_v1':
        from campaign_autumn_cost_revision import validate_request as validate_autumn_cost
        return validate_autumn_cost(request)
    if request.get('schema')=='continuous_campaign_residual_v1':
        from campaign_residual_completion import validate_request as validate_residual
        return validate_residual(request)
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
            if any(d.get('reason')=='erp_mapping_missing_or_not_unique'
                   for ds in value.get('exceptions',{}).values() for d in ds):
                # Report projection only; don't erase historical exceptions or
                # alter queues merely to make the current count look smaller.
                from campaign_catalog_repair import remaining_mapping_summary
                try:
                    current_snapshot=authority.resolve_snapshot(load(local.root/'resolved-snapshot.json'))
                    value=dict(value,mapping_summary=remaining_mapping_summary(current_snapshot,value['exceptions']))
                except (ValueError,OSError,KeyError) as exc:
                    value=dict(value,mapping_summary={'remaining_sku_count':None,'error_type':type(exc).__name__,
                                                       'controller_modified':False})
            results.append(dict(value,segment_id=segment['segment_id']))
            # Unknown external writes or security gates are never worked around
            # by another activity. Pure missing-input segments may be skipped.
            if value['status']=='blocked':break
        result={'status':'complete' if len(results)==len(plan['segments']) and all(r['status']=='complete' for r in results) else 'blocked',
                'all_signed_up':len(results)==len(plan['segments']) and all(r.get('all_signed_up') for r in results),
                'segments':results,'plan_id':plan['plan_id'],'legacy_fallback':False}
        from campaign_recording_evidence import collect, failure_handling
        recordings=[];recording_errors=[]
        observations={}
        for p in [*root.rglob('*-observation.json'),*root.rglob('reused-product-export.json')]:
            job=load(p)
            if not job.get('operation'):continue
            old=observations.get(job['job_id'])
            if old is None or (old.get('state')!='finished' and job.get('state')=='finished'):
                observations[job['job_id']]=job
        for job in observations.values():
            try:recordings.extend(collect(job,artifact_roots))
            except (OSError,ValueError,KeyError) as exc:
                recording_errors.append({'job_id':job.get('job_id'),'error_type':type(exc).__name__})
        result['recordings']=recordings
        result['recording_errors']=recording_errors
        result['failure_handling']=[entry for job in observations.values()
                                    if (entry:=failure_handling(job)) is not None]
        result['full_recording_verified']=bool(recordings) and not recording_errors
        from campaign_continuous_recovery import persist_run_outcome
        persist_run_outcome(root,result)
        return result
    finally:store.db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--request-id')
    mode.add_argument('--register-discovery',action='store_true')
    p.add_argument('--diagnostic-progress',action='store_true',help='Maintenance logs only; daily AI must not poll intermediate jobs')
    p.add_argument('--audit-existing',action='store_true',help='Only the mandatory read-only final audit of an existing completed run; never execute enrollment')
    recovery_mode=p.add_mutually_exclusive_group()
    recovery_mode.add_argument('--reconcile-discount',action='store_true')
    recovery_mode.add_argument('--reconcile-scope',action='store_true')
    recovery_mode.add_argument('--reconcile-signup',action='store_true')
    recovery_mode.add_argument('--reconcile-discount-window',action='store_true')
    args=p.parse_args()
    if args.register_discovery:
        if any((args.reconcile_discount,args.reconcile_scope,args.reconcile_signup,args.reconcile_discount_window)):
            raise ValueError('recovery_requires_existing_request')
        print(json.dumps(register_request(json.load(sys.stdin)),ensure_ascii=False));return
    import re
    if not re.fullmatch('[0-9a-f]{64}',args.request_id):raise ValueError('invalid_request_id')
    path=ROOT/'requests'/(args.request_id+'.json');request=load(path)
    if fingerprint(request)!=args.request_id:raise ValueError('request_identity_changed')
    secret=json.loads(sys.stdin.readline());edge=EdgeClient(secret.pop('token'))
    a=Authority()
    def finish(value):
        from campaign_final_audit import finalize
        return finalize(request,value,root=ROOT/'runs'/args.request_id,authority=a,
                        edge=edge,artifact_roots=secret['artifact_roots'])
    try:
        if request.get('schema')=='continuous_puta_existing_new_sku_v1':
            if any((args.reconcile_discount,args.reconcile_scope,args.reconcile_signup,args.reconcile_discount_window,args.audit_existing)):
                raise ValueError('puta_continuation_cannot_reconcile_other_claims')
            from campaign_puta_new_sku_completion import execute
            result=execute(request,root=ROOT/'runs'/args.request_id,authority=a,edge=edge,artifact_roots=secret['artifact_roots'])
            print(json.dumps(result,ensure_ascii=False));return
        if request.get('schema')=='continuous_verified_failure_resubmit_v1':
            if any((args.reconcile_discount,args.reconcile_scope,args.reconcile_signup,args.reconcile_discount_window,args.audit_existing)):
                raise ValueError('unchanged_resubmit_cannot_reconcile_other_claims')
            from campaign_verified_failure_resubmit import execute
            result=execute(request,root=ROOT/'runs'/args.request_id,authority=a,edge=edge,artifact_roots=secret['artifact_roots'])
            print(json.dumps(result,ensure_ascii=False));return
        if args.audit_existing:
            if any((args.reconcile_discount,args.reconcile_scope,args.reconcile_signup,args.reconcile_discount_window)):
                raise ValueError('audit_cannot_resume_business_actions')
            saved=load(ROOT/'runs'/args.request_id/'result.json')
            if saved.get('status')!='complete':raise ValueError('audit_requires_settled_execution')
            print(json.dumps(finish(saved),ensure_ascii=False));return
        if request.get('schema')=='continuous_campaign_autumn_cost_v1':
            if any((args.reconcile_discount,args.reconcile_scope,args.reconcile_signup,args.reconcile_discount_window)):
                raise ValueError('cost_request_cannot_reconcile_other_claims')
            from campaign_autumn_cost_revision import execute
            result=execute(request,root=ROOT/'runs'/args.request_id,authority=a,edge=edge,artifact_roots=secret['artifact_roots'])
            print(json.dumps(finish(result),ensure_ascii=False));return
        if request.get('schema')=='continuous_campaign_residual_v1':
            if any((args.reconcile_discount,args.reconcile_scope,args.reconcile_signup,args.reconcile_discount_window)):
                raise ValueError('residual_request_cannot_reconcile_old_claims')
            from campaign_residual_completion import execute
            result=execute(request,root=ROOT/'runs'/args.request_id,authority=a,edge=edge,artifact_roots=secret['artifact_roots'])
            print(json.dumps(finish(result),ensure_ascii=False));return
        if args.reconcile_discount_window:
            from campaign_continuous_recovery import recover_finished_discount_window
            recovery=recover_finished_discount_window(ROOT/'runs'/args.request_id,a,edge,artifact_roots=secret['artifact_roots'])
            print(json.dumps({'discount_window_reconciliation':recovery},ensure_ascii=False),flush=True)
        if args.reconcile_signup:
            from campaign_continuous_recovery import recover_finished_signup
            recovery=recover_finished_signup(ROOT/'runs'/args.request_id,a,edge)
            print(json.dumps({'signup_reconciliation':recovery},ensure_ascii=False),flush=True)
        if args.reconcile_scope:
            from campaign_continuous_recovery import recover_finished_scope
            recovery=recover_finished_scope(ROOT/'runs'/args.request_id,a,edge,artifact_roots=secret['artifact_roots'])
            print(json.dumps({'scope_reconciliation':recovery},ensure_ascii=False),flush=True)
        if args.reconcile_discount:
            from campaign_continuous_recovery import recover_finished_discount
            recovery=recover_finished_discount(ROOT/'runs'/args.request_id,a,edge)
            print(json.dumps({'discount_reconciliation':recovery},ensure_ascii=False),flush=True)
        from campaign_owned_execution import execute_owned
        result=execute_owned(request,root=ROOT/'runs'/args.request_id,authority=a,edge=edge,execute=execute_request,
            artifact_roots=secret['artifact_roots'],
            progress=(lambda job: print(json.dumps({'progress':job['state'],'job_id':job['job_id']},ensure_ascii=False),flush=True)) if args.diagnostic_progress else None)
        print(json.dumps(finish(result),ensure_ascii=False))
    finally:a.close()


if __name__=='__main__':main()

"""Fixed program segment: read five amounts, apply user grant, enroll this item.

Retains parent terminal/template/export; no other product is submitted or edited.
"""
from pathlib import Path
import json
import sys

import campaign_autumn_cost_revision as grant
import campaign_cost_revision as core
from campaign_entry_authority import load,file_sha
from campaign_continuous_transport import CampaignTransport,persist
from campaign_continuous_policy import fingerprint


def context(request):
    grant.validate_request(request)
    from campaign_residual_completion import TEMPLATE_JOB,TEMPLATE_SHA
    parent=grant.ROOT/'runs'/grant.PARENT
    original=load(grant.ROOT/'requests'/(grant.PARENT+'.json'))
    result=load(parent/'result.json')
    if fingerprint(original)!=grant.PARENT or result.get('status')!='complete':
        raise ValueError('autumn_parent_identity_or_terminal_changed')
    page=original['pages'][grant.CAMPAIGN]
    if any(page[k]!=v for k,v in grant.WINDOW.items()) or str(page['official_rate'])!='0.12' or page['shop_id']!='畔色木作':
        raise ValueError('autumn_exact_activity_changed')
    exceptions=[s['exceptions'].get(grant.ITEM,[]) for s in result['segments'] if s['segment_id']==grant.SEGMENT]
    if (len(exceptions)!=1 or {e.get('sku') for e in exceptions[0]}!=set(grant.SKUS)
            or any(e.get('reason')!='actual_reused_discount_outside_scoped_tolerance' for e in exceptions[0])):
        raise ValueError('autumn_original_five_price_gaps_changed')
    snapshot=load(grant.SNAPSHOT)
    if file_sha(grant.SNAPSHOT)!=grant.SNAPSHOT_SHA:raise ValueError('autumn_snapshot_changed')
    scope=load(parent/'shared'/'product-scope.json')
    if (scope.get('complete') is not True or grant.ITEM not in snapshot['current_sellable_item_ids']
            or not any(r['item']==grant.ITEM and r['on_sale'] is True for r in scope['platform_rows'])):
        raise ValueError('autumn_sellable_onsale_intersection_missing')
    matches=[load(p) for p in (parent/'segments'/grant.SEGMENT/'actions').glob('*/template-observation.json')
             if load(p).get('job_id')==TEMPLATE_JOB]
    if len(matches)!=1:raise ValueError('autumn_current_template_job_not_unique')
    job=matches[0];template=job['result']
    identity=CampaignTransport(None,None,root=grant.RUN/'context',request={},artifact_roots=[]).identity({'identity':page})
    if (job.get('state')!='finished' or job.get('operation')!='template' or template.get('identity')!=identity
            or template.get('source')!='official_current_template_download' or grant.ITEM not in template['items']
            or template['sha256']!=TEMPLATE_SHA or file_sha(template['path'])!=TEMPLATE_SHA):
        raise ValueError('autumn_original_official_template_changed')
    return dict(parent=parent,original=original,page=page,snapshot=snapshot,scope=scope,template=template)


class CostTransport(CampaignTransport):
    def __init__(self,*args,prepared,**kwargs):
        super().__init__(*args,**kwargs);self.prepared=prepared
        persist(self.root/'resolved-snapshot.json',prepared['snapshot'])
        persist(self.root/'product-scope.json',prepared['scope'])

    def execute(self,step,action_id,payload):
        if payload.get('items') and set(payload['items'])!={grant.ITEM}:
            raise ValueError('autumn_cost_completion_outside_one_item')
        return super().execute(step,action_id,payload)

    def step_scope(self,action_id,payload,folder):
        return self.with_prior(dict(self.prepared['scope'],erp_sellable=[grant.ITEM],price_version=grant.VERSION),payload,folder)

    def step_template(self,action_id,payload,folder):
        template=self.prepared['template']
        if file_sha(template['path'])!=template['sha256']:raise ValueError('autumn_template_changed')
        return dict(template,registered_items=[],reused_same_campaign_template=True)


def execute(request,*,root,authority,edge,artifact_roots):
    from campaign_continuous_flow import Store,run
    from campaign_segmented_time import bind_request
    from campaign_continuous_recovery import persist_run_outcome
    grant.validate_request(request);root=Path(root)
    if root.resolve()!=grant.RUN.resolve():raise ValueError('autumn_exact_run_required')
    persist(root/'request.json',request)
    stages=[]
    try:
        prepared=context(request)
        # No successful or unknown enrollment may be replayed by the price grant.
        blocked=authority.blocked(grant.CAMPAIGN,'signup',**grant.WINDOW)
        if grant.ITEM in blocked:raise ValueError('autumn_success_or_unknown_protected')
        core.check_same_segment_failures(authority,policy=grant)
        timing=str(prepared['parent']/'time-request.json')
        transport=CostTransport(edge,authority,root=root/'execution',request={'target':'big','time_request':timing},
                                artifact_roots=artifact_roots,prepared=prepared)
        identity=transport.identity({'identity':prepared['page']})
        # Exact old values are read once by the recorded driver, never caller money.
        transport.job('discount_readback',dict(identity=identity,read_request_id=grant.READ_ID,
            price_window=grant.WINDOW,offers=[dict(item=grant.ITEM,offer_id=grant.OFFER,sku_ids=list(grant.SKUS))]),root/'price')
        cid=core.claim(authority,policy=grant)['claim_id']
        persist(root/'price'/'claim.json',{'claim_id':cid,'grant_id':grant.GRANT})
        job=transport.job('discount_reprice',{'identity':identity,'claim_id':cid},root/'price')
        saved=core.record(authority,cid,job)
        persist(root/'price'/'verified.json',saved)
        store=Store(root/'controller.sqlite3')
        try:
            value=run(store,transport,prepared['page'],expected_shop='畔色木作',
                observed_links=prepared['original']['observed_links'],time_binding=bind_request(timing,grant.SEGMENT))
        finally:store.close()
        stages.append(dict(value,segment_id=grant.SEGMENT))
        result=dict(status=value['status'],all_signed_up=value.get('all_signed_up',False),segments=stages,
                    cost_revision=saved,excluded_items=['793052650673'],execution_mode='program_owned_terminal_only')
    except (ValueError,OSError,KeyError,RuntimeError) as exc:
        result=dict(status='blocked',all_signed_up=False,segments=[dict(segment_id=grant.SEGMENT,success={},pending=[],
            exceptions={grant.ITEM:[dict(action='manual',reason=str(exc))]})],
            excluded_items=['793052650673'],execution_mode='program_owned_terminal_only')
    persist_run_outcome(root,result)
    return result

"""One bounded continuation of Sep14 residual work, not a replay of 57 products.

Inputs contain identity/scope only. All prices, templates, fixed bases and failed
attempts are re-derived from retained authority. The coworker-owned cabinet is
excluded, and the original controller/claims/outcome evidence are never reset.
"""
from copy import deepcopy
import json
from pathlib import Path

from campaign_continuous_policy import RULE_SHA, fingerprint, classify_items
from campaign_continuous_transport import CampaignTransport, persist
from campaign_entry_authority import load, file_sha

PARENT='1bd5fc2953379e383bc1203c6d36c26541922eccee9561c06c68cf7d0efa97f8'
SEGMENT='485511952e858a1ccb16e04a7541eb6368ad2c5cb819b1701d4b1f897a7aac1f'
BOOK='805268708396'
EXCLUDED=['793052650673']
ITEMS=['722275846168','792992319206','793202812082',BOOK]
CAMPAIGN='49557/49560/3538210379'
TEMPLATE_JOB='50bd2e728f44810edef64eed47da71977e86e01c1b3fddd07c5c724aac15df72'
TEMPLATE_SHA='3d8b620ecf4c0327586643d8b8e64eff0c7fa4cb12023fab078631fb8c94443b'
ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-continuous')


def validate_request(request):
    expected={'schema':'continuous_campaign_residual_v1','rule_sha':RULE_SHA,
              'parent_request_id':PARENT,'items':ITEMS,'excluded_items':EXCLUDED}
    if request!=expected:raise ValueError('exact_residual_scope_required')
    return '畔色木作'


def source(request, *, root=ROOT):
    validate_request(request)
    parent=Path(root)/'runs'/PARENT
    original=load(Path(root)/'requests'/(PARENT+'.json'))
    if fingerprint(original)!=PARENT:raise ValueError('residual_parent_request_changed')
    state=load(parent/'result.json')
    if state.get('status')!='complete':raise ValueError('residual_parent_not_settled')
    page=original['pages'][CAMPAIGN]
    if (page['shop_id'],page['start'],page['end'],str(page['official_rate']))!=(
            '畔色木作','2026-09-16 20:00:00','2026-09-27 23:59:59','0.12'):
        raise ValueError('residual_autumn_identity_changed')
    return parent,original,page


def prepare_book(request, authority, edge, *, output, artifact_roots, root=ROOT):
    """Local evidence preparation only; no browser, price edit or submission."""
    from campaign_prior_failure_import import adopt_report
    from campaign_template_failure_supplement import supplement
    from campaign_feedback_normalization import normalize_errors
    parent,original,page=source(request,root=root)
    segment=parent/'segments'/SEGMENT
    snapshot=load(segment/'resolved-snapshot.json')
    scope=load(parent/'shared'/'product-scope.json')
    if (scope.get('complete') is not True or BOOK not in snapshot['current_sellable_item_ids']
            or not any(r['item']==BOOK and r['on_sale'] is True for r in scope['platform_rows'])):
        raise ValueError('book_not_in_verified_sellable_onsale_intersection')
    protected=authority.blocked(CAMPAIGN,'signup',page['start'],page['end'])
    if BOOK in protected:raise ValueError('book_success_or_unknown_protected')
    attempts=list(authority.db.execute('SELECT * FROM attempts WHERE item=? AND campaign=? AND phase=? '
        'AND start=? AND end=? ORDER BY rowid DESC',(BOOK,CAMPAIGN,'signup',page['start'],page['end'])))
    if not attempts or attempts[0]['status']!='failed':raise ValueError('book_exact_failed_attempt_required')
    latest=dict(attempts[0]);batch=str(json.loads(latest['evidence'])['batch'])
    if batch!='831931699':raise ValueError('book_newer_failure_requires_original_report_processing')
    observations=list(segment.glob('actions/*/template-observation.json'))
    matches=[(p,load(p)) for p in observations if load(p).get('job_id')==TEMPLATE_JOB]
    if len(matches)!=1:raise ValueError('book_official_template_evidence_not_unique')
    observation_path,job=matches[0];template=job['result']
    identity=CampaignTransport(edge,authority,root=Path(output)/'audit',request={},artifact_roots=artifact_roots).identity({'identity':page})
    if (job.get('state')!='finished' or job.get('operation')!='template'
            or template.get('identity')!=identity or template.get('source')!='official_current_template_download'
            or template.get('sha256')!=TEMPLATE_SHA or file_sha(template['path'])!=TEMPLATE_SHA
            or BOOK not in template.get('items',[])):
        raise ValueError('book_same_campaign_template_not_verified')
    audit=CampaignTransport(edge,authority,root=Path(output)/'audit',request={},artifact_roots=artifact_roots)
    persist(audit.root/'resolved-snapshot.json',snapshot)
    report=adopt_report(audit,{'identity':page},latest['id'].split(':')[0],[latest])
    body=authority.get_bundle(report['bundle_id'])
    original_errors=[e for e in report['errors'] if e['item']==BOOK]
    constraints=[deepcopy(c) for e in original_errors for c in e.get('constraints',[e])]
    supplemented=supplement(constraints,Path(template['path']).read_bytes(),expected_sha=TEMPLATE_SHA,
        evidence={'path':template['path'],'submitted_prices':{(r['item'],r['sku']):r['activity_price'] for r in body['signup_rows']}})
    errors=normalize_errors({'errors':supplemented},submitted_rows=body['signup_rows'],
        erp_rows=snapshot['all_erp_rows'],fixed_bases=authority.bases(snapshot),
        actual_discounts=[],rate=page['official_rate'],target_mode='big')
    repairs,exceptions=classify_items(errors)
    if exceptions or set(repairs)!={BOOK} or any(e.get('custom') is not True for e in errors):
        raise ValueError('book_complement_not_within_fixed_custom_rule')
    proof=dict(report,errors=errors,supplemental_template={'path':template['path'],'sha256':TEMPLATE_SHA,
        'observation_path':str(observation_path),'observation_sha256':file_sha(observation_path)},
        original_report_preserved=True,excluded_items=EXCLUDED)
    return dict(page=page,original=original,parent=parent,scope=scope,snapshot=snapshot,
                template=template,report=proof,attempt_id=latest['id'],repairs=repairs)


class BookTransport(CampaignTransport):
    def __init__(self,*args,prepared,**kwargs):
        super().__init__(*args,**kwargs);self.prepared=prepared
        persist(self.root/'resolved-snapshot.json',prepared['snapshot'])
        persist(self.root/'product-scope.json',prepared['scope'])
        persist(self.root/'reports'/(prepared['report']['batch']+'.json'),prepared['report'])

    def execute(self,step,action_id,payload):
        items=payload.get('items',[])
        if items and set(items)!={BOOK}:raise ValueError('residual_outside_exact_book_scope')
        if step in ('discount','signup','repair'):
            if BOOK in self.authority.blocked(CAMPAIGN,'signup',self.prepared['page']['start'],self.prepared['page']['end']):
                raise ValueError('residual_new_success_or_unknown_protected')
        return super().execute(step,action_id,payload)

    def step_scope(self,action_id,payload,folder):
        p=self.prepared;report=p['report']
        # Preserve the full verified product export/count. Narrow ERP eligibility
        # to this explicit failed product; do not manufacture a one-row export.
        return dict(p['scope'],erp_sellable=[BOOK],price_version=p['snapshot']['resolved_price_version_sha256'],
            prior_outcomes={BOOK:'failed'},prior_outcomes_evidence=report['source_terminal'],
            prior_failures={BOOK:{'batch':report['batch'],'errors':report['errors']}})

    def step_template(self,action_id,payload,folder):
        template=self.prepared['template']
        if file_sha(template['path'])!=TEMPLATE_SHA:raise ValueError('residual_template_changed')
        return dict(template,registered_items=[],reused_same_campaign_template=True)

    def recover_prior_failures(self,payload,items):return {}
    def recover_prior_price_gaps(self,payload,items):return {}
    def recover_missing_inputs(self,payload,exceptions):return {}


def execute(request,*,root,authority,edge,artifact_roots):
    from campaign_continuous_flow import Store,run
    from campaign_segmented_time import bind_request
    validate_request(request);root=Path(root)
    persist(root/'request.json',request)
    unresolved=[dict(item='792992319206',reason='autumn_cost_revision_not_covered_by_daily_grant'),
                dict(item='722275846168',reason='longterm_new_sku_amendment_entry_missing'),
                dict(item='793202812082',reason='longterm_new_sku_amendment_entry_missing')]
    results=[]
    try:
        prepared=prepare_book(request,authority,edge,output=root/'book',artifact_roots=artifact_roots)
        timing=prepared['parent']/'time-request.json'
        transport=BookTransport(edge,authority,root=root/'book'/'execution',request={'target':'big','time_request':str(timing)},
            artifact_roots=artifact_roots,prepared=prepared)
        store=Store(root/'book'/'controller.sqlite3')
        try:
            value=run(store,transport,prepared['page'],expected_shop='畔色木作',
                observed_links=prepared['original']['observed_links'],time_binding=bind_request(timing,SEGMENT))
            results.append(dict(value,segment_id=SEGMENT))
        finally:store.close()
    except (ValueError,OSError,KeyError) as exc:
        unresolved.append(dict(item=BOOK,reason=str(exc)))
    terminal_segments=results+[{'segment_id':'residual-unresolved','success':{},'pending':[],
        'exceptions':{r['item']:[{'action':'manual','reason':r['reason']}] for r in unresolved}}]
    result={'status':'blocked' if any(r.get('status')=='blocked' for r in results) else 'complete',
            'all_signed_up':False,'segments':terminal_segments,'remaining':unresolved,
            'excluded_items':EXCLUDED,'execution_mode':'program_owned_terminal_only',
            'parent_request_id':PARENT,'original_claims_reset':False,'automatic_rotation':False}
    from campaign_continuous_recovery import persist_run_outcome
    persist_run_outcome(root,result)
    return result

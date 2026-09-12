"""Concrete ERP -> retained Edge transport for the frozen continuous controller.

Browser actions are repository-defined jobs, not injected AI callbacks. Each
controller action keeps its file/job/claim checkpoint before any external write.
"""
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

from campaign_continuous_flow import REQUIRED_CAPABILITIES
from campaign_continuous_policy import fingerprint, RULE_SHA
from campaign_entry_authority import file_sha, load


def persist(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if load(path)!=value:raise ValueError('continuous_evidence_is_immutable')
    else:
        with path.open('x',encoding='utf-8') as out:json.dump(value,out,ensure_ascii=False,indent=2)
    return str(path.resolve())


class CampaignTransport:
    def __init__(self, edge, authority, *, root, request, artifact_roots, progress=None):
        self.edge,self.authority=edge,authority
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.request=request;self.roots=[Path(p) for p in artifact_roots]
        self.progress=progress

    def capabilities(self):
        # Implementation availability, not production/business acceptance.
        return sorted(REQUIRED_CAPABILITIES)

    def identity(self,payload):
        p=payload['identity']
        return {k:str(p[k]) for k in ('campaign_id','phase_id','sign_record_id','title','phase_title','start','end')} | {
            'shop_name':p['shop_id'],'rate_label':format((Decimal(str(p['official_rate']))*100).normalize(),'f')+'%'}

    def execute(self,step,action_id,payload):
        if payload.get('rule_sha')!=RULE_SHA:raise ValueError('continuous_rule_not_approved')
        folder=self.root/'actions'/action_id
        persist(folder/'request.json',{'step':step,'payload':payload})
        method=getattr(self,'step_'+step,None)
        if method is None:raise ValueError('unsupported_continuous_stage')
        result=method(action_id,payload,folder)
        result=dict(result,action_id=action_id,request_sha=fingerprint(payload))
        result.setdefault('status','terminal')
        # Preserve source evidence; never use this envelope to turn a partial
        # browser job or HTTP acknowledgement into a business terminal.
        evidence=persist(folder/'result.json',result)
        return dict(result,evidence=evidence)

    def job(self,step,payload,folder):
        ref=folder/(step+'-job.json')
        if ref.exists():
            job=self.edge.status(load(ref)['job_id'])
        else:
            job=self.edge.submit(step,payload)
            persist(ref,{'job_id':job['job_id'],'step':step})
        if job['state']=='running':
            job=self.edge.wait(job['job_id'],timeout=1800 if step=='product_export' else 300,progress=self.progress)
        persist(folder/(step+'-observation.json'),job)
        if job.get('state')!='finished':
            reason=(job.get('result') or {}).get('reason') or job['state']
            raise ValueError('edge_stage_not_terminal:'+str(reason))
        return job

    def step_scope(self,action_id,payload,folder):
        if getattr(self,'shared_scope',None):
            scope,source=self.shared_scope
            persist(self.root/'resolved-snapshot.json',load(source/'resolved-snapshot.json'))
            persist(self.root/'product-scope.json',scope)
            return self.with_prior(dict(scope),payload,folder)
        from campaign_price_snapshot import build_snapshot,load_rows
        from campaign_product_scope import from_edge_job,unique_mappings
        snapshot_path=self.root/'snapshot.json'
        if snapshot_path.exists():snapshot=load(snapshot_path)
        else:
            snapshot=self.authority.resolve_snapshot(build_snapshot(load_rows()))
            persist(snapshot_path,snapshot)
        existing=self.request.get('existing_product_export')
        if existing:
            # The daily task may have completed this same final test's export
            # while maintenance was deploying. Re-read its exact persisted job;
            # do not start another export or accept caller-supplied file content.
            export_request_id=existing['snapshot_request_id']
            job=self.edge.status(existing['job_id'])
            if job.get('job_id')!=existing['job_id']:
                raise ValueError('existing_product_export_job_identity_mismatch')
            persist(folder/'reused-product-export.json',job)
        else:
            export_request_id=action_id
            self.edge._action('inspect_product_export_setup',{})
            job=self.job('product_export',{'identity':self.identity(payload),'snapshot_request_id':action_id},folder)
        scope=from_edge_job(job,expected_request_id=export_request_id,expected_shop=payload['identity']['shop_id'],roots=self.roots)
        persist(self.root/'product-scope.json',scope)
        mapping=unique_mappings(scope,snapshot['all_erp_rows'])
        # This is a local, proven ID mapping overlay only; no business DB change.
        if mapping['matches']:
            doc={'status':'verified_partial_mapping_restored','restored':mapping['matches'],
                 'official_export_evidence':scope['page_evidence'],'database_write':False}
            path=persist(folder/'mapping.json',doc)
            self.authority.register_source(path,'mapping',file_sha(path))
            snapshot=self.authority.resolve_snapshot(snapshot)
        persist(self.root/'resolved-snapshot.json',snapshot)
        return self.with_prior(dict(scope,erp_sellable=snapshot['current_sellable_item_ids'],
            price_version=snapshot['resolved_price_version_sha256'],mapping_issues=mapping['unknown']),payload,folder)

    def with_prior(self,scope,payload,folder):
        p=payload['identity'];campaign='/'.join(str(p[k]) for k in ('campaign_id','phase_id','sign_record_id'))
        prior=self.authority.blocked(campaign,'signup',p['start'],p['end'])
        # Preserve explicit old failures as exceptions until their original
        # report is available; never reinterpret a missing record as fresh work.
        for row in self.authority.db.execute('SELECT item,status FROM attempts WHERE campaign=? AND phase=?',(campaign,'signup')):
            if row['item'] not in prior and row['status']=='failed':prior[row['item']]='failed'
        proof=persist(folder/'prior-outcomes.json',{'campaign':campaign,'items':prior})
        return dict(scope,prior_outcomes=prior,prior_outcomes_evidence=proof)

    def step_template(self,action_id,payload,folder):
        from campaign_official_template import template_rows
        if self.request.get('fixed_signup_template'):
            from campaign_fixed_signup_template import resolve_master
            result=resolve_master(self.request['fixed_signup_template'],identity=self.identity(payload),roots=self.roots)
        else:
            job=self.job('template',{'identity':self.identity(payload),'items':payload['items']},folder)
            result=job.get('result') or {}
        if (result.get('state')!='downloaded' or result.get('source')!='official_current_template_download'
                or (not result.get('fixed_master') and sorted(result.get('items',[]))!=sorted(payload['items']))
                or file_sha(result['path'])!=result['sha256']):raise ValueError('official_template_not_verified')
        # A cached fixed master is structural evidence, not today's enrollment
        # state. For a fresh template, one successful SKU cannot hide failures
        # of other SKUs in the same product.
        states=defaultdict(list)
        if not result.get('fixed_master'):
            for row in template_rows(Path(result['path']).read_bytes()):
                if row['item'] in payload['items']:states[row['item']].append(row['state'])
        registered=sorted(item for item,values in states.items() if values and
            all(value in ('活动中','进行中','已生效','已发布设定') for value in values))
        return dict(result,registered_items=registered)

    def step_generate(self,action_id,payload,folder):
        from campaign_generate_current_files import _generate
        from campaign_failure_remediation import corrected_scope
        snapshot_path=self.root/'resolved-snapshot.json'
        scoped_corrections={i:ds for i,ds in payload.get('corrections',{}).items() if i in payload['items']}
        exclusion_refs={d['repair']['scope_evidence']['path']:d['repair']['scope_evidence']
                        for ds in scoped_corrections.values() for d in ds
                        if d['repair']['kind']=='exclude_ineligible_sku'}
        activity_template=Path(payload['template']['path'])
        if payload['template'].get('fixed_master'):
            from campaign_fixed_template_projection import project
            source=activity_template
            if file_sha(source)!=payload['template']['sha256']:
                raise ValueError('fixed_template_changed_before_generation')
            scope=load(self.root/'product-scope.json')
            scope,mapping_facts=corrected_scope(scope,scoped_corrections)
            missing=set(payload['items'])-{e['facts']['item'] for e in scope['sku_facts']}
            if missing:
                return {'input_issues':[dict(item=i,sku='',error='no_remaining_eligible_sku_after_official_export')
                    for i in sorted(missing)],'items':payload['items']}
            if mapping_facts:
                refs={d['repair']['scope_evidence']['path'] for ds in scoped_corrections.values()
                      for d in ds if d['repair']['kind']=='exclude_ineligible_sku'}
                files=[]
                for ref in sorted(refs):
                    proof=load(load(ref)['scope']['page_evidence']['path'])
                    files.extend({'path':f['path'],'sha256':f['sha256']} for f in proof['files'])
                unique={f['sha256']:f for f in files}
                overlay=persist(folder/'official-mapping-overlay.json',{'facts':mapping_facts,'files':list(unique.values())})
                snapshot=dict(self.authority.resolve_snapshot(load(snapshot_path)),
                    official_mapping_overlay={'path':overlay,'sha256':file_sha(overlay)})
                from campaign_failure_remediation import mapping_conflicts
                issues=mapping_conflicts(snapshot,mapping_facts)
                if issues:return {'input_issues':issues,'items':payload['items']}
                snapshot_path=Path(persist(folder/'snapshot-with-id-overlay.json',snapshot))
            projected=project(source.read_bytes(),scope,payload['items'])
            activity_template=folder/'fixed-master-current-skus.xlsx'
            if activity_template.exists():
                if activity_template.read_bytes()!=projected:
                    raise ValueError('fixed_projection_changed')
            else:
                with activity_template.open('xb') as stream:stream.write(projected)
            persist(folder/'fixed-projection.json',{
                'master_path':str(source),'master_sha256':payload['template']['sha256'],
                'projection_path':str(activity_template),'projection_sha256':file_sha(activity_template),
                'product_export_evidence':scope['page_evidence'],
                'sku_scope':'all_current_export_skus_no_stock_filter',
                'platform_write':False,'stock_or_listing_switch_changed':False})
        p=payload['identity'];timing=payload.get('time_binding')
        segment=timing['segment'] if timing else None
        window=segment['price_window'] if segment else {k:p[k] for k in ('start','end')}
        wanted={(i,d['sku']):d['repair']['price'] for i,decisions in payload.get('corrections',{}).items()
                if i in payload['items'] for d in decisions if d['repair']['kind']=='custom_price'}
        custom={}
        for receipt in (self.root/'repairs').glob('custom-*.json'):
            for r in load(receipt)['rows']:
                pair=(r['item'],r['sku'])
                if wanted.get(pair)==r['activity_price']:custom[pair]=r
        if set(custom)!=set(wanted):raise ValueError('custom_correction_authority_missing')
        corrections=folder/'custom-corrections.json'
        if custom:persist(corrections,{'rows':list(custom.values())})
        args=SimpleNamespace(snapshot=snapshot_path,activity_template=activity_template,
            campaign_key='/'.join(str(p[k]) for k in ('campaign_id','phase_id','sign_record_id')),
            official_rate=str(p['official_rate']),target=segment['target'] if segment else self.request['target'],
            start=window['start'],end=window['end'],custom_basis_receipt=[],signup_items=','.join(payload['items']),
            discount_items=','.join(payload['items']),continuous_rule_sha=RULE_SHA,output_dir=folder/'files',
            custom_corrections=corrections if corrections.exists() else None,
            sku_exclusion_receipts=list(exclusion_refs.values()),
            time_request=Path(self.request['time_request']) if timing else None,
            time_segment=segment['segment_id'] if segment else None)
        result=_generate(args,self.authority)
        if result['issues']:
            return {'input_issues':result['issues'],'items':payload['items'],'source_evidence':str(args.output_dir/'receipt.json')}
        body=self.authority.get_bundle(result['entry_bundle_id'])
        return dict(bundle_id=result['entry_bundle_id'],items=payload['items'],validated_rule_sha=RULE_SHA,
                    price_version=result['price_version'],file_sha=fingerprint(result['files']),full_active_skus=True,
                    discount_items=sorted({r['item'] for r in body['discount_rows']}),
                    time_binding=timing,source_evidence=str(args.output_dir/'receipt.json'))

    def submit_phase(self,phase,payload,folder):
        from campaign_submission_gate import validated_body
        from campaign_edge_receipt import reconcile_signup
        from campaign_discount_receipt import reconcile_discount
        bundle=payload['bundle']['bundle_id'];ref=folder/'claim.json'
        if ref.exists():cid=load(ref)['claim_id']
        else:
            body,_=validated_body(self.authority,bundle,phase)
            if sorted({r['item'] for r in body[phase+'_rows']})!=sorted(payload['items']):
                raise ValueError('phase_scope_does_not_match_actual_file')
            cid=self.authority.claim(bundle,phase,transport='dedicated_edge_v1')
            persist(ref,{'claim_id':cid})
        job=self.job(phase,{'identity':self.identity(payload),'claim_id':cid},folder)
        result=(reconcile_signup if phase=='signup' else reconcile_discount)(self.authority,job,output_dir=folder)
        persist(self.root/'terminals'/(phase+'-'+str(result['batch'])+'.json'),dict(result,bundle_id=bundle))
        return result

    def step_discount(self,action_id,payload,folder):
        self.edge._action('inspect_discount_setup',{'open_if_missing':True})
        return self.submit_phase('discount',payload,folder)

    def step_signup(self,action_id,payload,folder):
        return self.submit_phase('signup',payload,folder)

    def step_verify_discount_window(self,action_id,payload,folder):
        from campaign_discount_readback import verify
        body=self.authority.get_bundle(payload['bundle']['bundle_id'])
        wanted={(r['item'],r['sku']) for r in body['planned_discount_rows']}
        expected=[];offers=[]
        for offer in self.authority.discount_offers():
            if (offer['start'],offer['end'])!=(body['start'],body['end']):continue
            actual_id=offer.get('platform_offer_id',offer['offer_id'])
            successful={r['item'] for r in offer['items'] if r['status']=='success'}
            groups=defaultdict(list)
            for r in offer['rows']:
                if (r['item'],r['sku']) in wanted and r['item'] in successful:
                    groups[r['item']].append(r['sku']);expected.append(dict(r,offer_id=actual_id))
            offers.extend(dict(offer_id=actual_id,item=i,sku_ids=s) for i,s in groups.items())
        if {(r['item'],r['sku']) for r in expected}!=wanted:
            raise ValueError('saved_discount_full_scope_missing')
        if not wanted:
            return dict(all_correct=True,items=payload['items'],start=body['start'],end=body['end'],
                        no_single_discount_required=True)
        job=self.job('discount_readback',{'identity':self.identity(payload),'read_request_id':action_id,
            'price_window':{k:body[k] for k in ('start','end')},'offers':offers},folder)
        result=verify(job,read_request_id=action_id,shop=payload['identity']['shop_id'],start=body['start'],end=body['end'],
                      expected_rows=expected,roots=self.roots)
        persist(self.root/'verified-discounts'/str(payload['bundle']['bundle_id']+'.json'),dict(result,rows=expected))
        return dict(result,items=payload['items'])

    def step_report(self,action_id,payload,folder):
        from campaign_feedback_normalization import normalize_errors
        from campaign_failure_remediation import report_from_download, resolve_invalid_skus
        result=load(self.root/'terminals'/(payload['failed_phase']+'-'+str(payload['batch'])+'.json'))
        if not result.get('errors'):
            result=report_from_download(self,result,payload,folder)
        body=self.authority.get_bundle(result['bundle_id']);snapshot=load(body['snapshot_path'])
        path=self.root/'verified-discounts'/(result['bundle_id']+'.json')
        readback=load(path) if path.exists() else {'rows':[]}
        actual=[dict(r,verified_readback=True,evidence=readback.get('evidence'),target=body['target']) for r in readback['rows']]
        errors=normalize_errors(result,submitted_rows=body['signup_rows'],erp_rows=snapshot['all_erp_rows'],
            fixed_bases=self.authority.bases(snapshot),actual_discounts=actual,rate=body['official_rate'],target_mode=body['target'])
        errors=[e for e in errors if e['item'] in payload['items']]
        try:
            errors=resolve_invalid_skus(self,errors,payload,folder)
        except (ValueError,OSError,KeyError) as exc:
            # The export failure holds only the affected mapping items; pricing
            # repairs on unrelated failed products must still progress.
            errors=[dict(e,kind='unknown',parse_issue='mapping_export_unavailable:'+str(exc))
                    if e.get('kind')=='mapping' else e for e in errors]
        report=dict(errors=errors,batch=payload['batch'],bundle_id=result['bundle_id'],source_terminal=result['evidence'])
        persist(self.root/'reports'/(str(payload['batch'])+'.json'),report)
        return report

    def step_repair(self,action_id,payload,folder):
        from campaign_continuous_repairs import execute_repairs
        return execute_repairs(self,action_id,payload,folder)

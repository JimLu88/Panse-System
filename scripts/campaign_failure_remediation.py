"""User-authorized 2026-09-12 failure handling. Fixed jobs, never AI browser control.

Original claims, batches, prices and version fingerprints are immutable. A new
report or listing snapshot is additive evidence, not permission to replay them.
"""
from copy import deepcopy
from pathlib import Path

from campaign_entry_authority import file_sha, load
from campaign_continuous_policy import fingerprint


def report_from_download(transport, terminal, payload, folder):
    """Adopt an already downloaded exact feedback job; never click again here."""
    from campaign_official_failure_report import parse_feedback
    from campaign_continuous_transport import persist
    bound={'identity':transport.identity(payload),'batch':str(payload['batch'])}
    job=transport.edge.status(fingerprint(['feedback',bound]))
    result=job.get('result') or {}
    proof=load(terminal['evidence'])
    observation=load(proof['official_observation']['path'])
    if file_sha(proof['official_observation']['path'])!=proof['official_observation']['sha256']:
        raise ValueError('original_official_batch_changed')
    if (job.get('operation')!='feedback' or job.get('state')!='finished'
            or result.get('state')!='downloaded' or result.get('batch')!=str(payload['batch'])
            or result.get('record')!=observation['record']):
        raise ValueError('official_failure_report_missing_no_redownload')
    path=Path(result['path']).resolve(strict=True)
    if not any(path.is_relative_to(p.resolve()) for p in transport.roots):
        raise ValueError('feedback_outside_artifact_roots')
    body=transport.authority.get_bundle(terminal['bundle_id'])
    parsed=parse_feedback(path.read_bytes(),expected_sha=result['sha256'],batch=str(payload['batch']),
        expected_items=sorted({r['item'] for r in body['signup_rows']}),official_counts=observation['counts'])
    if sorted((r['item'],r['outcome']) for r in parsed['outcomes'])!=sorted(
            (r['item'],r['outcome']) for r in terminal['items']):
        raise ValueError('feedback_conflicts_with_original_terminal')
    expected={(r['item'],r['sku']) for r in body['signup_rows'] if r['item'] in {g['item'] for g in parsed['groups']}}
    actual={(r['item'],r['sku']) for g in parsed['groups'] for r in g['rows']}
    if expected!=actual:raise ValueError('feedback_sku_scope_changed')
    # HTTP adds `ok`; disk readback does not. It is transport metadata, not
    # a different official receipt. Preserve the original canonical evidence.
    persist(folder/'adopted-feedback-observation.json',{k:v for k,v in job.items() if k!='ok'})
    return dict(terminal,errors=parsed['errors'],feedback=result)


def reusable_failure_export(transport, payload):
    """Reuse this unchanged controller's complete export, never a global cache.

    The controller has no product-edit/rotation operation. Its immutable ERP
    snapshot and exact campaign/shop bind this cache; a changed catalog needs a
    new controller/snapshot. Revalidate the live job and every exported file.
    """
    from campaign_product_scope import from_edge_job
    context=fingerprint([transport.identity(payload),file_sha(transport.root/'resolved-snapshot.json')])
    for path in sorted((transport.root/'repairs').glob('scope-*.json'),reverse=True):
        doc=load(path)
        if doc.get('rule')!='failure-remediation-20260912':continue
        if doc.get('catalog_context_sha256',context)!=context:continue
        proof=(doc.get('scope') or {}).get('page_evidence') or {}
        request_id=fingerprint(['failed-sku-export',transport.identity(payload),str(doc['batch'])])
        # A reused receipt retains its originating export request, not the new
        # failure batch. Its binding was written by this same fixed function.
        request_id=doc.get('export_request_id',request_id)
        job=transport.edge.status(proof['job_id'])
        scope=from_edge_job(job,expected_request_id=request_id,
            expected_shop=payload['identity']['shop_id'],roots=transport.roots)
        if scope!=doc['scope']:raise ValueError('cached_product_export_scope_changed')
        return scope,request_id,context
    return None,None,context


def resolve_invalid_skus(transport, errors, payload, folder):
    """One complete export for the unchanged catalog, reused by later failures."""
    from campaign_product_scope import from_edge_job
    from campaign_continuous_transport import persist
    from campaign_continuous_policy import classify
    invalid=[e for e in errors if e.get('kind')=='mapping']
    if not invalid:return errors
    scope,request_id,context=reusable_failure_export(transport,payload)
    if scope is None:
        request_id=fingerprint(['failed-sku-export',transport.identity(payload),str(payload['batch'])])
        transport.edge._action('inspect_product_export_setup',{})
        job=transport.job('product_export',{'identity':transport.identity(payload),
            'snapshot_request_id':request_id},folder/'mapping-export')
        scope=from_edge_job(job,expected_request_id=request_id,
            expected_shop=payload['identity']['shop_id'],roots=transport.roots)
    excluded=sorted({(e['item'],e['sku']) for e in invalid
                     if e.get('official_invalid_or_disabled') is True})
    if not excluded:raise ValueError('official_rejected_sku_scope_missing')
    # Exports lack the enabled toggle. Platform's explicit not-owned/disabled
    # failure is the exclusion proof even if that inactive SKU is exported.
    receipt={'scope':scope,'excluded':[{'item':i,'sku':s} for i,s in excluded],
        'batch':str(payload['batch']),'errors':invalid,'rule':'failure-remediation-20260912',
        'product_deleted':False,'stock_modified':False,
        'export_request_id':request_id,'catalog_context_sha256':context}
    target=transport.root/'repairs'/('scope-'+str(payload['batch'])+'.json')
    if target.exists():
        prior=load(target)
        if any(prior.get(k)!=receipt.get(k) for k in prior):raise ValueError('sku_scope_evidence_changed')
        receipt=prior  # Previously signed receipts remain byte-for-byte immutable.
    path=persist(target,receipt)
    ref={'path':path,'sha256':file_sha(path)}
    return [dict(e,full_official_export_verified=True,remove_from_signup=True,mapping_scope_evidence=ref)
            if (e.get('item'),e.get('sku')) in excluded else e for e in errors]


def corrected_scope(scope, corrections):
    """Replace only failed product facts. Other products keep their snapshot."""
    value=deepcopy(scope);refs={}
    for item,decisions in corrections.items():
        for decision in decisions:
            repair=decision['repair']
            if repair['kind']=='exclude_ineligible_sku':
                ref=repair['scope_evidence'];refs[ref['path']]=ref
    mappings=[];documents=[];all_excluded=set()
    for ref in refs.values():
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('sku_scope_evidence_changed')
        doc=load(ref['path']);fresh=doc['scope']
        if fresh.get('complete') is not True or not fresh.get('page_evidence'):
            raise ValueError('complete_product_export_required')
        excluded={(r['item'],r['sku']) for r in doc['excluded']}
        all_excluded.update(excluded);documents.append((fresh,excluded))
    for fresh,excluded in documents:
        items={i for i,s in excluded if i in corrections}
        selected=[e for e in fresh['sku_facts'] if e['facts']['item'] in items
                  and (e['facts']['item'],e['facts']['sku']) not in all_excluded]
        value['sku_facts']=[e for e in value['sku_facts'] if e['facts']['item'] not in items]+selected
        mappings.extend(selected)
    mappings=list({(e['facts']['item'],e['facts']['sku']):e for e in mappings}.values())
    return value,mappings


def excluded_pairs(refs):
    """Revalidate file-only exclusions during generation AND before upload."""
    result=set()
    for ref in refs:
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('sku_exclusion_evidence_changed')
        doc=load(ref['path'])
        if doc.get('rule')!='failure-remediation-20260912' or doc['scope'].get('complete') is not True:
            raise ValueError('sku_exclusion_full_export_missing')
        rejected={(e['item'],e['sku']) for e in doc['errors'] if e.get('kind')=='mapping'
                  and e.get('official_invalid_or_disabled') is True and e.get('terminal')=='failed'
                  and e.get('batch')==doc['batch'] and e.get('official_evidence')}
        requested={(r['item'],r['sku']) for r in doc['excluded']}
        if not requested or not requested.issubset(rejected):raise ValueError('sku_exclusion_not_officially_rejected')
        result.update(requested)
    return result


def verified_code_aliases(snapshot):
    """Exact registered backup codes only; never strip a B1/B2 suffix by guess."""
    result=set()
    from campaign_catalog_repair import documents
    for doc in documents(snapshot):
        for row in doc.get('restored',[]):
            result.add((row['item'],row['sku'],row['erp_code'],row['official_sku_code']))
    for ref in snapshot.get('verified_code_alias_sources',[]):
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('backup_alias_receipt_changed')
        doc=load(ref['path'])
        if doc.get('status')!='verified_partial_mapping_restored':raise ValueError('backup_alias_not_verified')
        for row in doc['restored']:
            if not row.get('official_sku_code'):continue
            if not row.get('alias_evidence'):raise ValueError('backup_alias_provenance_missing')
            for source in row['alias_evidence']:
                if file_sha(source['path'])!=source['sha256']:raise ValueError('backup_alias_source_changed')
            result.add((row['item'],row['sku'],row['erp_code'],row['official_sku_code']))
    return result


def mapped_erp_rows(snapshot):
    """Apply only official ID aliases; monetary snapshot/version stay unchanged."""
    from campaign_product_scope import parse_export
    from campaign_catalog_repair import mapped_rows
    rows=mapped_rows(snapshot,snapshot['all_erp_rows'])
    overlay=snapshot.get('official_mapping_overlay')
    if not overlay:return rows
    if file_sha(overlay['path'])!=overlay['sha256']:raise ValueError('mapping_overlay_changed')
    doc=load(overlay['path']);raw_rows={};aliases=verified_code_aliases(snapshot)
    for entry in doc['facts']:
        fact=entry['facts'];code=fact['sku_code'];pair=fact['item'],fact['sku']
        proofs=[]
        for source in entry['sources']:
            matches=[f for f in doc['files'] if f['sha256']==source['sha256']]
            if len(matches)!=1:raise ValueError('mapping_export_file_not_unique')
            f=matches[0]
            if f['sha256'] not in raw_rows:
                if file_sha(f['path'])!=f['sha256']:raise ValueError('mapping_export_changed')
                raw_rows[f['sha256']]=parse_export(Path(f['path']).read_bytes())
            proofs.extend(r for r in raw_rows[f['sha256']] if (r['item'],r['sku'],r['sku_code'])==(*pair,code))
        if not proofs:raise ValueError('mapping_not_in_official_export')
        candidates=[r for r in rows if r['code']==code and pair[0] in {
            str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}]
        existing=[r for r in rows if pair[0] in {str(r.get('item')),str(r.get('product_item_id')),
            *map(str,r.get('product_alt_item_ids') or [])} and pair[1] in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
        if existing:
            if len(existing)!=1 or (code and existing[0]['code']!=code and (*pair,existing[0]['code'],code) not in aliases):
                raise ValueError('mapping_conflicts_with_current_erp')
            continue
        if len(candidates)!=1:continue  # Generation isolates the precise unmatched item.
        candidates[0]['alt']=list(dict.fromkeys([*map(str,candidates[0].get('alt') or []),pair[1]]))
    return rows


def mapping_conflicts(snapshot, facts):
    """Isolate semantic code conflicts to their product; do not halt peers."""
    issues=[];aliases=verified_code_aliases(snapshot)
    for entry in facts:
        f=entry['facts'];pair=f['item'],f['sku'];code=f['sku_code']
        bound=[r for r in snapshot['all_erp_rows'] if pair[0] in {str(r.get('item')),
            str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}
            and pair[1] in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
        if len(bound)>1 or (len(bound)==1 and code and bound[0]['code']!=code and (*pair,bound[0]['code'],code) not in aliases):
            issues.append(dict(item=pair[0],sku=pair[1],error='official_code_conflicts_with_erp_mapping'))
    return issues

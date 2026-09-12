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
    persist(folder/'adopted-feedback-observation.json',job)
    return dict(terminal,errors=parsed['errors'],feedback=result)


def resolve_invalid_skus(transport, errors, payload, folder):
    """One complete recorded export per failed batch, with exact ID/code checks."""
    from campaign_product_scope import from_edge_job
    from campaign_continuous_transport import persist
    from campaign_continuous_policy import classify
    invalid=[e for e in errors if e.get('kind')=='mapping']
    if not invalid:return errors
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
        'product_deleted':False,'stock_modified':False}
    path=persist(transport.root/'repairs'/('scope-'+str(payload['batch'])+'.json'),receipt)
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
    mappings=[]
    for ref in refs.values():
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('sku_scope_evidence_changed')
        doc=load(ref['path']);fresh=doc['scope']
        if fresh.get('complete') is not True or not fresh.get('page_evidence'):
            raise ValueError('complete_product_export_required')
        excluded={(r['item'],r['sku']) for r in doc['excluded']}
        items={i for i,s in excluded if i in corrections}
        selected=[e for e in fresh['sku_facts'] if e['facts']['item'] in items
                  and (e['facts']['item'],e['facts']['sku']) not in excluded]
        value['sku_facts']=[e for e in value['sku_facts'] if e['facts']['item'] not in items]+selected
        mappings.extend(selected)
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


def mapped_erp_rows(snapshot):
    """Apply only official ID aliases; monetary snapshot/version stay unchanged."""
    from campaign_product_scope import parse_export
    rows=deepcopy(snapshot['all_erp_rows'])
    overlay=snapshot.get('official_mapping_overlay')
    if not overlay:return rows
    if file_sha(overlay['path'])!=overlay['sha256']:raise ValueError('mapping_overlay_changed')
    doc=load(overlay['path']);raw_rows={}
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
            if len(existing)!=1 or (code and existing[0]['code']!=code):
                raise ValueError('mapping_conflicts_with_current_erp')
            continue
        if len(candidates)!=1:continue  # Generation isolates the precise unmatched item.
        candidates[0]['alt']=list(dict.fromkeys([*map(str,candidates[0].get('alt') or []),pair[1]]))
    return rows

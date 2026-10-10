"""Adopt exact locally retained failure reports; never download or replay claims."""
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from campaign_entry_authority import file_sha, load
from campaign_continuous_transport import persist
from campaign_official_failure_report import parse_feedback
from campaign_feedback_normalization import normalize_errors


def report_reference(terminal):
    """Accept both historical spellings without modifying pinned terminals."""
    refs=[]
    for prefix in ('source_report','official_report'):
        path_key=prefix if prefix=='source_report' else prefix+'_path'
        sha_key=prefix+'_sha256'
        if path_key in terminal or sha_key in terminal:
            if not terminal.get(path_key) or not terminal.get(sha_key):
                raise ValueError('prior_report_reference_incomplete')
            refs.append((terminal[path_key],terminal[sha_key]))
    feedback=terminal.get('feedback')
    if feedback is not None:
        if (not isinstance(feedback,dict) or not feedback.get('path') or not feedback.get('sha256')
                or str(feedback.get('batch'))!=str(terminal.get('batch_id'))):
            raise ValueError('prior_feedback_reference_invalid')
        refs.append((feedback['path'],feedback['sha256']))
    if not refs or any(r!=refs[0] for r in refs):
        raise ValueError('prior_report_reference_missing_or_conflicting')
    return refs[0]


def retained_report(transport,payload,batch):
    """Read an existing exact activity/batch job, never start a new download."""
    from campaign_continuous_policy import fingerprint
    bound={'identity':transport.identity(payload),'batch':str(batch)}
    jid=fingerprint(['feedback',bound]);job=transport.edge.status(jid)
    result=job.get('result') or {}
    if (job.get('job_id')!=jid or job.get('operation')!='feedback' or job.get('state')!='finished'
            or result.get('state')!='downloaded' or str(result.get('batch'))!=str(batch)
            or not result.get('path') or not result.get('sha256')):
        raise ValueError('prior_same_sha_report_not_recovered')
    return result['path'],result['sha256'],{k:v for k,v in job.items() if k!='ok'}


def restored_report(transport,payload,batch,sha):
    """A missing previously pinned file requires the very same bytes."""
    path,actual,job=retained_report(transport,payload,batch)
    if actual!=sha:raise ValueError('prior_same_sha_report_not_recovered')
    return path,job


def import_prior(transport,payload,items,*,readback=False):
    p=payload['identity'];campaign='/'.join(str(p[k]) for k in ('campaign_id','phase_id','sign_record_id'))
    a=transport.authority;protected=a.blocked(campaign,'signup',p['start'],p['end'])
    wanted=set(items)-set(protected);latest={}
    for row in a.db.execute('SELECT * FROM attempts WHERE campaign=? AND phase=? AND start=? AND end=? ORDER BY rowid DESC',
                            (campaign,'signup',p['start'],p['end'])):
        if row['item'] in wanted:latest.setdefault(row['item'],dict(row))
    groups=defaultdict(list)
    for row in latest.values():
        if row['status']=='failed':groups[row['id'].split(':')[0]].append(row)
    imported={};issues=[]
    for claim,rows in groups.items():
        try:
            report=adopt_report(transport,payload,claim,rows,readback=readback)
            for row in rows:
                errors=[e for e in report['errors'] if e['item']==row['item']]
                if not errors:raise ValueError('prior_report_item_errors_missing')
                imported[row['item']]={'batch':report['batch'],'errors':errors}
        except (ValueError,OSError,KeyError,TypeError) as exc:
            issues.append({'claim_id':claim,'items':[r['item'] for r in rows],'reason':str(exc)})
    if issues:
        from campaign_continuous_policy import fingerprint
        persist(transport.root/'prior-import'/('issues-'+fingerprint(issues)+'.json'),issues)
    return imported


def adopt_report(transport,payload,claim,rows,*,readback=False):
    a=transport.authority;p=payload['identity'];ref=json.loads(rows[0]['evidence'])
    if any(json.loads(r['evidence'])!=ref or r['bundle_id']!=rows[0]['bundle_id'] for r in rows):
        raise ValueError('prior_claim_evidence_conflict')
    def checked(path,sha):
        path=Path(path).resolve(strict=True)
        if not any(path.is_relative_to(r.resolve()) for r in transport.roots):
            raise ValueError('prior_report_outside_artifact_roots')
        if file_sha(path)!=sha:raise ValueError('prior_source_changed')
        return path
    source=checked(ref['path'],ref['sha256']);terminal=load(source)
    body=a.get_bundle(rows[0]['bundle_id'])
    from campaign_segmented_time import phase_window
    # The original validated bundle owns its price segment. Signup receipts
    # belong to the official activity window, not that discount segment.
    signup_window=phase_window(body,'signup')
    identity=('/'.join(str(p[k]) for k in ('campaign_id','phase_id','sign_record_id')),p['start'],p['end'])
    if (terminal.get('schema')!='campaign_entry_terminal_v1' or terminal.get('claim_id')!=claim
            or terminal.get('phase')!='signup' or terminal.get('terminal') is not True
            or tuple(terminal.get(k) for k in ('campaign','start','end'))!=identity
            or (body.get('campaign'),signup_window['start'],signup_window['end'])!=identity
            or str(terminal.get('batch_id'))!=str(ref['batch'])):
        raise ValueError('prior_terminal_identity_mismatch')
    claimed=list(a.db.execute('SELECT * FROM attempts WHERE id LIKE ?', (claim+':%',)))
    original={r['item']:r['status'] for r in terminal['items']}
    if original!={r['item']:r['status'] for r in claimed} or any(r['bundle_id']!=rows[0]['bundle_id'] for r in claimed):
        raise ValueError('prior_terminal_claim_scope_changed')
    submitted={(r['item'],r['sku']):r for r in body['signup_rows']}
    if set(original)!={i for i,s in submitted}:raise ValueError('prior_bundle_scope_changed')
    files=[f for f in body['files'] if f['sha256']==terminal['file_sha256']]
    if len(files)!=1:raise ValueError('prior_submitted_file_binding_missing')
    checked(files[0]['path'],files[0]['sha256'])
    recovery=None
    has_reference=(terminal.get('feedback') is not None or any(k in terminal for k in (
        'source_report','source_report_sha256','official_report_path','official_report_sha256')))
    if has_reference:
        report_path,report_sha=report_reference(terminal)
        try:raw=checked(report_path,report_sha)
        except FileNotFoundError:
            report_path,recovery=restored_report(transport,payload,ref['batch'],report_sha)
            raw=checked(report_path,report_sha)
    else:
        # A batch terminal may precede its separately completed feedback job.
        # Require its deterministic identity and all original scope/price
        # checks below; do not rewrite the older terminal to attach a file.
        report_path,report_sha,recovery=retained_report(transport,payload,ref['batch'])
        raw=checked(report_path,report_sha)
    parsed=parse_feedback(raw.read_bytes(),expected_sha=report_sha,batch=str(ref['batch']),
                          expected_items=sorted(original))
    if {r['item']:r['outcome'] for r in parsed['outcomes']}!=original:
        raise ValueError('prior_feedback_conflicts_with_claim')
    report_rows={(r['item'],r['sku']):r for g in parsed['groups'] for r in g['rows']}
    if set(report_rows)!=set(submitted) or any(Decimal(r['submitted_price'])!=Decimal(submitted[k]['activity_price'])
                                              for k,r in report_rows.items()):
        raise ValueError('prior_feedback_submitted_sku_or_price_changed')
    if recovery is not None:
        persist(transport.root/'prior-import'/(str(ref['batch'])+'-restored-report.json'),
                {'source_terminal':str(source),'source_terminal_sha256':ref['sha256'],
                 'original_report_reference':list(report_reference(terminal)) if has_reference else None,'recovered':recovery,
                 'report_sha256':report_sha,'claims_changed':False,'platform_write':False})
    snapshot=load(transport.root/'resolved-snapshot.json')
    # Current frozen ERP version/bases, original submitted price and official
    # constraints. Never treat a historical deduction as live readback.
    errors=normalize_errors(parsed,submitted_rows=body['signup_rows'],erp_rows=snapshot['all_erp_rows'],
        fixed_bases=a.bases(snapshot),actual_discounts=[],rate=p['official_rate'],target_mode=body['target'])
    if readback:
        path=transport.root/'verified-discounts'/(rows[0]['bundle_id']+'.json')
        saved=read_discount_gaps(transport,payload,errors,body,path)
        actual=[dict(r,verified_readback=True,evidence=saved['evidence'],target=body['target']) for r in saved['rows']]
        errors=normalize_errors(parsed,submitted_rows=body['signup_rows'],erp_rows=snapshot['all_erp_rows'],
            fixed_bases=a.bases(snapshot),actual_discounts=actual,rate=p['official_rate'],target_mode=body['target'])
    report={'errors':errors,'batch':str(ref['batch']),'bundle_id':rows[0]['bundle_id'],
            'source_terminal':str(source),'prior_terminal_sha256':ref['sha256'],
            'current_snapshot_sha256':file_sha(transport.root/'resolved-snapshot.json')}
    target=(transport.root/'prior-import'/(str(ref['batch'])+'-readback-report.json') if readback
            else transport.root/'reports'/(str(ref['batch'])+'.json'))
    # Approved first-price evidence may arrive after the initial report import.
    # Keep the original normalization immutable, save a content-addressed new
    # interpretation of the SAME verified official terminal/file/price scope.
    if target.exists() and load(target)!=report:
        old=load(target)
        for key in ('batch','bundle_id','source_terminal','prior_terminal_sha256','current_snapshot_sha256'):
            if old.get(key)!=report.get(key):raise ValueError('prior_reclassification_source_changed')
        from campaign_continuous_policy import fingerprint
        target=target.parent/(target.stem+'-classification-'+fingerprint(report)+'.json')
    persist(target,report)
    return report


def read_discount_gaps(transport,payload,errors,body,path):
    """One fixed read for only missing ordinary amounts, no new price/export."""
    from campaign_continuous_policy import fingerprint
    from campaign_discount_readback import verify
    wanted={(e['item'],e['sku']) for e in errors if e.get('parse_issue')=='exact_existing_discount_readback_missing'}
    if not wanted:raise ValueError('no_exact_discount_gaps_to_read')
    expected=[];offers=[]
    for offer in transport.authority.discount_offers():
        if (offer['start'],offer['end'])!=(body['start'],body['end']):continue
        oid=offer.get('platform_offer_id',offer['offer_id'])
        success={r['item'] for r in offer['items'] if r['status']=='success'}
        partial=set(map(tuple,offer.get('verified_partial_skus',[])));groups=defaultdict(list)
        for r in offer['rows']:
            if (r['item'],r['sku']) in wanted and (r['item'] in success or (r['item'],r['sku']) in partial):
                groups[r['item']].append(r['sku']);expected.append(dict(r,offer_id=oid))
        offers.extend(dict(offer_id=oid,item=i,sku_ids=ss) for i,ss in groups.items())
    if len(expected)!=len(wanted) or {(r['item'],r['sku']) for r in expected}!=wanted:
        raise ValueError('prior_discount_gap_saved_amount_missing_or_duplicate')
    rid=fingerprint(['prior-discount-gap',body['campaign'],body['start'],body['end'],expected])
    folder=transport.root/'prior-import'/rid
    job=transport.job('discount_readback',{'identity':transport.identity(payload),'read_request_id':rid,
        'price_window':{k:body[k] for k in ('start','end')},'offers':offers},folder)
    result=verify(job,read_request_id=rid,shop=payload['identity']['shop_id'],start=body['start'],end=body['end'],
                  expected_rows=expected,roots=transport.roots)
    if result.get('all_correct') is not True:raise ValueError('prior_discount_actual_amount_changed')
    saved=dict(result,rows=expected);persist(path,saved)
    return saved

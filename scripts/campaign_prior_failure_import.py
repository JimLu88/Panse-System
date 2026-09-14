"""Adopt exact locally retained failure reports; never download or replay claims."""
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from campaign_entry_authority import file_sha, load
from campaign_continuous_transport import persist
from campaign_official_failure_report import parse_feedback
from campaign_feedback_normalization import normalize_errors


def import_prior(transport,payload,items):
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
            report=adopt_report(transport,payload,claim,rows)
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


def adopt_report(transport,payload,claim,rows):
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
    identity=('/'.join(str(p[k]) for k in ('campaign_id','phase_id','sign_record_id')),p['start'],p['end'])
    if (terminal.get('schema')!='campaign_entry_terminal_v1' or terminal.get('claim_id')!=claim
            or terminal.get('phase')!='signup' or terminal.get('terminal') is not True
            or tuple(terminal.get(k) for k in ('campaign','start','end'))!=identity
            or tuple(body.get(k) for k in ('campaign','start','end'))!=identity
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
    raw=checked(terminal['source_report'],terminal['source_report_sha256'])
    parsed=parse_feedback(raw.read_bytes(),expected_sha=terminal['source_report_sha256'],batch=str(ref['batch']),
                          expected_items=sorted(original))
    if {r['item']:r['outcome'] for r in parsed['outcomes']}!=original:
        raise ValueError('prior_feedback_conflicts_with_claim')
    report_rows={(r['item'],r['sku']):r for g in parsed['groups'] for r in g['rows']}
    if set(report_rows)!=set(submitted) or any(Decimal(r['submitted_price'])!=Decimal(submitted[k]['activity_price'])
                                              for k,r in report_rows.items()):
        raise ValueError('prior_feedback_submitted_sku_or_price_changed')
    snapshot=load(transport.root/'resolved-snapshot.json')
    # Current frozen ERP version/bases, original submitted price and official
    # constraints. Never treat a historical deduction as live readback.
    errors=normalize_errors(parsed,submitted_rows=body['signup_rows'],erp_rows=snapshot['all_erp_rows'],
        fixed_bases=a.bases(snapshot),actual_discounts=[],rate=p['official_rate'],target_mode=body['target'])
    report={'errors':errors,'batch':str(ref['batch']),'bundle_id':rows[0]['bundle_id'],
            'source_terminal':str(source),'prior_terminal_sha256':ref['sha256'],
            'current_snapshot_sha256':file_sha(transport.root/'resolved-snapshot.json')}
    persist(transport.root/'reports'/(str(ref['batch'])+'.json'),report)
    return report

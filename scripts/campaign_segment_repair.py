"""Bridge an immutable signup failure to an explicitly selected discount segment.

Local evidence/claim only. The existing recorded discount_amend job remains the
sole writer. Never retarget a controller, mutate its bundle, or release a claim.
"""
from decimal import Decimal
from pathlib import Path
import json
import re

from campaign_entry_authority import file_sha, load, exact_campaign
from campaign_continuous_policy import RULE_SHA, classify_items, fingerprint
from campaign_feedback_normalization import normalize_errors


def pinned(ref):
    if set(ref) != {'path', 'sha256'} or file_sha(ref['path']) != ref['sha256']:
        raise ValueError('segment_repair_source_changed')
    return load(ref['path'])


def derive(authority, request):
    """Recompute, don't trust caller-supplied amounts or normalized decisions."""
    if set(request) != {'schema','campaign','shop_name','start','end','offer_id','readback','failures'}:
        raise ValueError('segment_repair_request_invalid')
    if request['schema'] != 'campaign_segment_repair_v1':
        raise ValueError('segment_repair_schema_invalid')
    campaign = exact_campaign(request['campaign'])
    from datetime import datetime
    start, end = (datetime.strptime(request[k], '%Y-%m-%d %H:%M:%S') for k in ('start','end'))
    if start > end: raise ValueError('segment_repair_window_invalid')
    read = pinned(request['readback'])
    if (read.get('state') != 'readback' or read.get('platform_write') is not False
            or read.get('shop_name') != request['shop_name']
            or read.get('price_window') != {k:request[k] for k in ('start','end')}):
        raise ValueError('segment_repair_exact_readback_required')
    actual = {}
    for group in read['rows']:
        if (group['window']['offer_id'] != request['offer_id']
                or any(group['window'][k] != request[k] for k in ('start','end'))):
            raise ValueError('segment_repair_readback_window_mismatch')
        for sku, amount in group['values'].items():
            pair = group['item'], sku
            if pair in actual: raise ValueError('segment_repair_duplicate_readback')
            actual[pair] = dict(item=pair[0],sku=sku,deduct=amount,target='medium',
                               verified_readback=True,evidence=request['readback'])
    if not actual: raise ValueError('segment_repair_empty_scope')
    blocked = authority.blocked(campaign,'signup',request['start'],request['end'])
    if any(item in blocked for item,sku in actual):
        raise ValueError('segment_repair_success_or_unknown_protected')
    offers = [o for o in authority.discount_offers()
              if o.get('platform_offer_id',o['offer_id']) == request['offer_id']
              and (o['start'],o['end']) == (request['start'],request['end'])]
    if len(offers) != 1: raise ValueError('segment_repair_registered_offer_required')
    offer = offers[0]
    for pair, row in actual.items():
        saved = [r for r in offer['rows'] if (r['item'],r['sku']) == pair]
        if (len(saved) != 1 or Decimal(saved[0]['deduct']) != Decimal(row['deduct'])
                or not any(i['item']==pair[0] and i['status']=='success' for i in offer['items'])):
            raise ValueError('segment_repair_registered_amount_mismatch')
    errors=[]; seen=set(); versions=set(); sources=[request['readback']]
    for failure in request['failures']:
        if set(failure) != {'report','claim_id'}: raise ValueError('segment_repair_failure_invalid')
        if not re.fullmatch('[0-9a-f]{32}',str(failure['claim_id'])):
            raise ValueError('segment_repair_claim_identity_invalid')
        report=pinned(failure['report']);sources.append(failure['report'])
        bundle=authority.get_bundle(report['bundle_id'])
        if (bundle['campaign'] != campaign or bundle['target'] != 'medium'
                or Decimal(bundle['official_rate']) != Decimal('.1')):
            raise ValueError('segment_repair_same_daily_campaign_required')
        if file_sha(bundle['snapshot_path']) != bundle['snapshot_sha256']:
            raise ValueError('segment_repair_original_snapshot_changed')
        snapshot=load(bundle['snapshot_path'])
        versions.add(snapshot['resolved_price_version_sha256'])
        terminal=load(report['source_terminal'])
        if (terminal.get('claim_id') != failure['claim_id'] or terminal.get('campaign') != campaign
                or terminal.get('phase') != 'signup' or terminal.get('terminal') is not True
                or str(terminal.get('batch_id')) != str(report['batch'])):
            raise ValueError('segment_repair_original_terminal_mismatch')
        attempts=list(authority.db.execute('SELECT * FROM attempts WHERE id LIKE ?',(failure['claim_id']+':%',)))
        target_errors=[e for e in report['errors'] if (e['item'],e['sku']) in actual]
        if not target_errors: raise ValueError('segment_repair_unrelated_failure')
        for e in target_errors:
            pair=e['item'],e['sku']
            if pair in seen: raise ValueError('segment_repair_duplicate_failure')
            seen.add(pair)
            matches=[a for a in attempts if a['item']==e['item'] and a['status']=='failed'
                     and a['campaign']==campaign and a['phase']=='signup' and a['bundle_id']==report['bundle_id']]
            if len(matches)!=1: raise ValueError('segment_repair_registered_failure_required')
            ref=json.loads(matches[0]['evidence'])
            if file_sha(ref['path'])!=ref['sha256'] or load(ref['path'])!=terminal:
                raise ValueError('segment_repair_registered_terminal_changed')
        constraints=[c for e in target_errors for c in e.get('constraints',[e])]
        normalized=normalize_errors({'errors':constraints},submitted_rows=bundle['signup_rows'],
            erp_rows=snapshot['all_erp_rows'],fixed_bases={},actual_discounts=list(actual.values()),
            rate=bundle['official_rate'],target_mode='medium')
        errors.extend(normalized)
        sources.extend({'path':str(p),'sha256':file_sha(p)}
                       for p in (bundle['snapshot_path'], report['source_terminal']))
    if seen!=set(actual) or len(versions)!=1: raise ValueError('segment_repair_complete_same_version_required')
    repairs, exceptions=classify_items(errors)
    if exceptions or any(e.get('custom') is not False for e in errors):
        raise ValueError('segment_repair_not_automatic_ordinary_scope')
    rows=[]
    for e in errors:
        ds=[d for d in repairs.get(e['item'],[]) if d['sku']==e['sku']]
        if len(ds)!=1 or ds[0]['repair']['kind']!='ordinary_discount':
            raise ValueError('segment_repair_not_small_discount')
        if Decimal(e['proposed_deduct']) <= Decimal(e['current_deduct']):
            raise ValueError('segment_repair_no_actual_change')
        rows.append(dict(item=e['item'],sku=e['sku'],offer_id=request['offer_id'],
                         old_deduct=e['current_deduct'],new_deduct=e['proposed_deduct']))
    return dict(errors=errors,rows=rows,sources=sources,price_version=next(iter(versions)))


def prepare(authority, request_path, output, *, claim=False):
    from campaign_continuous_transport import persist
    from campaign_continuous_repairs import table, verify_claim
    request_path=Path(request_path);request=load(request_path)
    derived=derive(authority,request);out=Path(output)
    report_path=persist(out/'segment-repair-report.json',{'errors':derived['errors']})
    body=dict(rule_sha=RULE_SHA,campaign=request['campaign'],start=request['start'],end=request['end'],
              rows=derived['rows'],report_path=report_path,
              segment_bridge={'path':str(request_path.resolve()),'sha256':file_sha(request_path)},
              sources=derived['sources']+[{'path':report_path,'sha256':file_sha(report_path)},
                                        {'path':str(request_path.resolve()),'sha256':file_sha(request_path)}])
    result=dict(state='prepared',rows=derived['rows'],price_version=derived['price_version'],platform_write=False)
    if claim:
        table(authority)
        # Stable identity across filenames/runs: same failure + offer + changes
        # cannot produce a fresh dispatch merely by choosing another directory.
        cid=fingerprint({'scope':{k:request[k] for k in ('campaign','start','end','offer_id')},
                         'rows':derived['rows'],'claims':sorted(f['claim_id'] for f in request['failures'])})[:32]
        authority.db.execute('BEGIN IMMEDIATE')
        try:
            old=authority.db.execute('SELECT * FROM continuous_discount_repairs WHERE id=?',(cid,)).fetchone()
            if old:
                if old['state']!='claimed_not_dispatched': raise ValueError('segment_repair_already_dispatched_no_replay')
            else:
                pairs={(r['item'],r['sku']) for r in derived['rows']}
                for prior in authority.db.execute('SELECT body FROM continuous_discount_repairs WHERE state IN (?,?)',
                                                   ('claimed_not_dispatched','dispatched_unknown')):
                    previous=json.loads(prior['body'])
                    if (previous['start']<=request['end'] and request['start']<=previous['end']
                            and pairs.intersection((r['item'],r['sku']) for r in previous['rows'])):
                        raise ValueError('segment_repair_existing_claim_protected')
                authority.db.execute('INSERT INTO continuous_discount_repairs VALUES(?,?,?,NULL,NULL)',
                    (cid,json.dumps(body,ensure_ascii=False),'claimed_not_dispatched'))
            verify_claim(authority,cid)
            authority.db.execute('COMMIT')
        except BaseException:
            if authority.db.in_transaction: authority.db.execute('ROLLBACK')
            raise
        result.update(state='claimed_not_dispatched',claim_id=cid)
    persist(out/'segment-repair-preparation.json',result)
    return result


if __name__=='__main__':
    import argparse
    from campaign_entry_authority import Authority
    p=argparse.ArgumentParser();p.add_argument('--request',required=True)
    p.add_argument('--output',required=True);p.add_argument('--claim',action='store_true')
    args=p.parse_args();a=Authority()
    try: print(json.dumps(prepare(a,args.request,args.output,claim=args.claim),ensure_ascii=False))
    finally:a.close()

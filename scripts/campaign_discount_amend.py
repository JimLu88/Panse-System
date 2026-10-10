"""Scoped existing-discount edit guard. No browser, upload or automatic retry.

CAS is local + a fresh owner-supplied platform read; it is NOT a platform atomic
transaction. The sole browser owner must compare expected old amounts in the
same edit surface and modify only payload rows, then provide actual readback.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from campaign_entry_authority import Authority, file_sha, load
from campaign_generate_current_files import official_cut
from campaign_official_template import discount_rate
from campaign_price_snapshot import digest
from campaign_scoped_tolerance import policy_for

AUTHORITY_FILE = Path(__file__).resolve().parents[1]/'docs/receipts/campaign-autumn-discount-amend-authority-20260911.json'
AUTHORITY_SHA256 = '3287fe2ff60c90d98c0597f14060e67cbfe0aa67556eb7c682f1c831f444bfb0'


def authorization():
    if file_sha(AUTHORITY_FILE)!=AUTHORITY_SHA256:raise ValueError('amend_authorization_changed')
    return load(AUTHORITY_FILE)


def pinned(ref):
    if file_sha(ref['path'])!=ref['sha256']:raise ValueError('amend_evidence_changed')
    return load(ref['path'])


def money(value):
    x=Decimal(str(value))
    if not x.is_finite() or x<=0 or x!=x.quantize(Decimal('.01')):raise ValueError('amend_invalid_money')
    return x


def pairs(rows):
    index={(r['item'],r['sku']):r for r in rows}
    if not rows or len(index)!=len(rows):raise ValueError('amend_empty_or_duplicate_scope')
    return index


def instant(value):
    t=datetime.fromisoformat(value)
    if t.tzinfo is None:raise ValueError('amend_evidence_timezone_required')
    return t


def validate_request(a, request):
    grant=authorization()
    if request.get('schema')!='campaign_discount_amend_request_v1' or request.get('authorization_sha256')!=AUTHORITY_SHA256:
        raise ValueError('amend_request_authority_not_bound')
    for key in ('campaign','start','end','offer_id','failed_signup_claim'):
        if request.get(key)!=grant[key]:raise ValueError('amend_outside_current_authorization:'+key)
    policy=policy_for(request['campaign'],request['start'],request['end'],Decimal('.12'),'big')
    if not policy:raise ValueError('amend_final_tolerance_missing')
    changes=pairs(request['rows'])
    failure=pinned(request['failure_rows'])
    if failure.get('schema')!='campaign_discount_failure_rows_v1' or failure.get('claim_id')!=request['failed_signup_claim']:
        raise ValueError('amend_failure_not_bound')
    if failure.get('campaign')!=request['campaign'] or failure['source']['sha256']!=grant['failure_report_sha256']:
        raise ValueError('amend_failure_report_mismatch')
    if file_sha(failure['source']['path'])!=grant['failure_report_sha256']:raise ValueError('amend_original_failure_report_changed')
    failures=pairs(failure['rows'])
    all_offers=a.discount_offers()
    offers=[o for o in all_offers if o['offer_id']==request['offer_id']]
    if len(offers)!=1:raise ValueError('amend_existing_offer_missing_or_ambiguous')
    offer=offers[0]
    if (offer['start'],offer['end'])!=(request['start'],request['end']):raise ValueError('amend_offer_window_changed')
    amounts=pairs(offer['rows']);statuses={r['item']:r['status'] for r in offer['items']}
    blocked=a.blocked(request['campaign'],'signup',request['start'],request['end'])
    resolved=[]
    for pair,change in changes.items():
        if pair[0] in blocked:raise ValueError('amend_signup_success_or_unknown_protected')
        attempt=a.db.execute('SELECT * FROM attempts WHERE id=?',(request['failed_signup_claim']+':'+pair[0],)).fetchone()
        if not attempt or attempt['phase']!='signup' or attempt['status']!='failed' or not attempt['evidence']:
            raise ValueError('amend_official_failed_claim_required')
        evidence=json.loads(attempt['evidence']);terminal=pinned(evidence)
        if terminal.get('claim_id')!=request['failed_signup_claim'] or not terminal.get('terminal') or terminal.get('batch_id')!=evidence['batch']:
            raise ValueError('amend_failed_terminal_changed')
        body=a.get_bundle(attempt['bundle_id'])
        if any(body[k]!=request[k] for k in ('campaign','start','end')):raise ValueError('amend_failed_window_mismatch')
        if discount_rate(body['official_rate'])!=Decimal('.12') or body['target']!='big':raise ValueError('amend_failed_price_mode_mismatch')
        row=pairs(body['signup_rows']).get(pair)
        f=failures.get(pair)
        if not f or f.get('status')!='failed' or not f.get('platform_error') or f.get('issue_kind')!='coupon_final_price':
            raise ValueError('amend_exact_failed_sku_required')
        if not row or row.get('custom') is not False:raise ValueError('amend_ordinary_only')
        if money(f['signup_price'])!=money(row['activity_price']):raise ValueError('amend_failed_sku_price_mismatch')
        if file_sha(body['snapshot_path'])!=body['snapshot_sha256']:raise ValueError('amend_snapshot_changed')
        snapshot=a.resolve_snapshot(load(body['snapshot_path']))
        candidates=[r for r in snapshot['all_erp_rows'] if r['code']==row['erp_code']]
        if len(candidates)!=1 or candidates[0].get('custom') is not False:raise ValueError('amend_current_mapping_or_classification_changed')
        erp=candidates[0];daily=money(erp['daily']);target=money(erp['big_target'])
        if pair[0] not in {str(erp.get('item')),str(erp.get('product_item_id')),*map(str,erp.get('product_alt_item_ids') or [])} or pair[1] not in {str(erp.get('sku')),*map(str,erp.get('alt') or [])}:
            raise ValueError('amend_current_sku_identity_changed')
        if money(row['activity_price'])!=daily or money(row['target'])!=target:raise ValueError('amend_current_daily_or_target_changed')
        old,new=money(change['old_deduct']),money(change['new_deduct'])
        if new==old or abs(new-old)>money(grant['maximum_amount_adjustment_cny']):raise ValueError('amend_outside_small_exact_change')
        if statuses.get(pair[0])!='success' or pair not in amounts or money(amounts[pair]['deduct'])!=old:
            raise ValueError('amend_expected_old_amount_cas_failed')
        overlap=[o for o in all_offers if o['start']<=request['end'] and request['start']<=o['end'] and any(i['item']==pair[0] and i['status'] in ('success','unknown') for i in o['items'])]
        if len(overlap)!=1:raise ValueError('amend_overlapping_offer')
        final=daily-official_cut(daily,Decimal('.12'))-new
        if final<=0 or abs(final-target)>Decimal(policy['max_absolute_delta_cny']):raise ValueError('amend_new_final_outside_authorized_tolerance')
        resolved.append(dict(change,erp_code=row['erp_code'],daily=str(daily),target=str(target),final=str(final),delta=str(final-target)))
    return dict(request=request,rows=resolved,final_price_tolerance=policy,operation='edit_existing_sku_deduct_only')


def verify_readback(doc, body, *, claim_id=None):
    request=body['request']
    if doc.get('schema')!='campaign_discount_amount_readback_v1':raise ValueError('amend_readback_schema')
    for key in ('campaign','start','end','offer_id'):
        if doc.get(key)!=request[key]:raise ValueError('amend_readback_identity_mismatch')
    if claim_id is not None and doc.get('claim_id')!=claim_id:raise ValueError('amend_readback_claim_mismatch')
    if not doc.get('source') or file_sha(doc['source']['path'])!=doc['source']['sha256']:
        raise ValueError('amend_platform_readback_source_missing_or_changed')
    now=datetime.now(timezone.utc);seen=instant(doc['observed_at'])
    if seen>now:raise ValueError('amend_readback_from_future')
    return pairs(doc['rows']),seen


def claim(a, request_path, before_path):
    request=load(request_path);identity=digest(request)
    a.db.execute('BEGIN IMMEDIATE')
    try:
        body=validate_request(a,request)
        before=load(before_path);actual,seen=verify_readback(before,body)
        if (datetime.now(timezone.utc)-seen).total_seconds()>300:raise ValueError('amend_before_readback_stale')
        if set(actual)!=set(pairs(body['rows'])):raise ValueError('amend_before_scope_mismatch')
        for row in body['rows']:
            if money(actual[(row['item'],row['sku'])]['deduct'])!=money(row['old_deduct']):raise ValueError('amend_platform_old_amount_cas_failed')
        body['before']={'path':str(Path(before_path).resolve()),'sha256':file_sha(before_path)}
        body['request_source']={'path':str(Path(request_path).resolve()),'sha256':file_sha(request_path)}
        claimed_at=datetime.now(timezone.utc).isoformat()
        a.db.execute('INSERT INTO discount_amendment_batches VALUES(?,?,?)',(identity,json.dumps(body,ensure_ascii=False),claimed_at))
        for row in body['rows']:
            a.db.execute('INSERT INTO discount_amendment_rows VALUES(?,?,?,?,?,?,?)',
                (identity,row['item'],row['sku'],request['failed_signup_claim'],request['offer_id'],'unknown',None))
        a.db.execute('COMMIT')
        return dict(claim_id=identity,operation=body['operation'],campaign=request['campaign'],offer_id=request['offer_id'],
                    start=request['start'],end=request['end'],rows=body['rows'],automatic_retry=False,
                    expected_old_compare_required=True,platform_write=False)
    except BaseException:
        a.db.execute('ROLLBACK');raise


def record(a, identity, receipt_path):
    batch=a.db.execute('SELECT * FROM discount_amendment_batches WHERE id=?',(identity,)).fetchone()
    if not batch:raise ValueError('amend_claim_not_found')
    body=json.loads(batch['body']);receipt=load(receipt_path)
    actual,seen=verify_readback(receipt,body,claim_id=identity)
    if seen<instant(batch['claimed_at']):raise ValueError('amend_readback_predates_write_claim')
    if not receipt.get('terminal') or not receipt.get('operation_reference'):raise ValueError('amend_official_terminal_missing')
    expected=pairs(body['rows'])
    if not set(actual)<=set(expected):raise ValueError('amend_readback_expanded_scope')
    updates=[]
    for pair,row in actual.items():
        status=row.get('status')
        if status not in ('success','failed'):continue
        wanted=expected[pair]['new_deduct' if status=='success' else 'old_deduct']
        if money(row['deduct'])!=money(wanted):raise ValueError('amend_terminal_amount_not_verified')
        updates.append((pair,status))
    proof=json.dumps({'path':str(Path(receipt_path).resolve()),'sha256':file_sha(receipt_path)},ensure_ascii=False)
    a.db.execute('BEGIN IMMEDIATE')
    try:
        for pair,status in updates:
            prior=a.db.execute('SELECT status,evidence FROM discount_amendment_rows WHERE claim_id=? AND item=? AND sku=?',(identity,*pair)).fetchone()
            if prior['status']!='unknown':
                if prior['status']!=status or prior['evidence']!=proof:raise ValueError('amend_terminal_conflict')
                continue
            a.db.execute('UPDATE discount_amendment_rows SET status=?,evidence=? WHERE claim_id=? AND item=? AND sku=?',(status,proof,identity,*pair))
        a.db.execute('COMMIT')
    except BaseException:
        a.db.execute('ROLLBACK');raise
    return [dict(r) for r in a.db.execute('SELECT item,sku,status FROM discount_amendment_rows WHERE claim_id=?',(identity,))]


def apply_confirmed(a, offers):
    """Overlay only verified exact rows; unknown locks the affected whole item."""
    offers=deepcopy(offers);by_id={o['offer_id']:o for o in offers}
    for batch in a.db.execute('SELECT * FROM discount_amendment_batches ORDER BY rowid'):
        body=json.loads(batch['body']);request=body['request'];offer=by_id.get(request['offer_id'])
        if not offer or (offer['start'],offer['end'])!=(request['start'],request['end']):raise ValueError('amend_parent_offer_missing_or_changed')
        index=pairs(offer['rows']);expected=pairs(body['rows'])
        for state in a.db.execute('SELECT * FROM discount_amendment_rows WHERE claim_id=?',(batch['id'],)):
            pair=state['item'],state['sku'];change=expected[pair]
            if pair not in index or money(index[pair]['deduct'])!=money(change['old_deduct']):raise ValueError('amend_history_old_amount_chain_changed')
            if state['status']=='unknown':
                for item in offer['items']:
                    if item['item']==pair[0]:item['status']='unknown'
                continue
            proof=json.loads(state['evidence']);doc=pinned(proof)
            actual,seen=verify_readback(doc,body,claim_id=batch['id'])
            row=actual.get(pair)
            wanted=change['new_deduct' if state['status']=='success' else 'old_deduct']
            if not row or row.get('status')!=state['status'] or money(row['deduct'])!=money(wanted) or seen<instant(batch['claimed_at']):
                raise ValueError('amend_saved_readback_invalid')
            if state['status']=='success':
                index[pair]['deduct']=str(money(change['new_deduct']))
                index[pair]['amendment_receipt']=dict(proof,claim_id=batch['id'])
    return offers


def run_once(a, request_path, read_before, edit_once, read_after):
    """Owner supplies three bounded operations. Errors leave durable unknown."""
    request=load(request_path)
    before_path=read_before(request)
    payload=claim(a,request_path,before_path)
    edit_once(payload)
    receipt_path=read_after(payload)
    return dict(claim_id=payload['claim_id'],states=record(a,payload['claim_id'],receipt_path))


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    c=sub.add_parser('claim');c.add_argument('--request',type=Path,required=True);c.add_argument('--before',type=Path,required=True)
    r=sub.add_parser('record');r.add_argument('--claim',required=True);r.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args();a=Authority()
    try:
        result=claim(a,args.request,args.before) if args.command=='claim' else record(a,args.claim,args.receipt)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:a.close()


if __name__=='__main__':main()

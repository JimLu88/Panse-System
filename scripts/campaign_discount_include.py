"""Add only proven missing ordinary SKU rows to their existing exact offer.

No overlap creation, ordinary repricing, custom edits, or unknown-write retry.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import argparse
import json
import re
import uuid

from campaign_entry_authority import Authority, file_sha, load, exact_campaign
from campaign_generate_current_files import official_cut
from campaign_failure_remediation import mapped_erp_rows


def table(a):
    a.db.execute('CREATE TABLE IF NOT EXISTS missing_discount_includes(id TEXT PRIMARY KEY,body TEXT NOT NULL,state TEXT NOT NULL,job_id TEXT,receipt TEXT)')


def pinned(ref):
    if file_sha(ref['path'])!=ref['sha256']:raise ValueError('include_source_changed')
    return load(ref['path'])


def derive(a,request):
    if request.get('schema')!='campaign_missing_discount_include_v1':raise ValueError('include_schema_invalid')
    exact_campaign(request['campaign'])
    snapshot=a.resolve_snapshot(pinned(request['snapshot']))
    if snapshot['resolved_price_version_sha256']!=request['price_version']:raise ValueError('include_price_version_changed')
    coverage=pinned(request['coverage']);read=pinned(request['missing_read'])
    window=dict(start=request['start'],end=request['end'])
    if (coverage.get('filtered_offer_list_coverage_verified') is not True or coverage.get('price_window')!=window
            or coverage.get('platform_write') is not False):raise ValueError('include_offer_coverage_not_proven')
    seen=set();items=set(coverage['items'])
    if not 0<len(items)<=2:raise ValueError('include_item_scope_invalid')
    for result in coverage['results']:
        source=pinned(dict(path=result['source_path'],sha256=result['source_sha256']))
        import importlib.util
        parser_path=Path('D:/AI/畔色ERP系统/Web-Agent程序/app/engine/campaign_discount_item_coverage.py')
        spec=importlib.util.spec_from_file_location('verified_offer_coverage',parser_path)
        parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
        checked=parser.check(source,request['start'],request['end'])
        if any(result.get(k)!=v for k,v in checked.items()):raise ValueError('include_coverage_projection_changed')
        if (source['item'],source['mode'])!=(result['item'],result['mode']):raise ValueError('include_coverage_identity_changed')
        key=result['item'],result['mode']
        if key in seen:raise ValueError('include_duplicate_coverage')
        seen.add(key)
        overlaps=[o for o in result['offers'] if o['overlaps_requested_window']]
        if result['mode']=='商品级' and overlaps:raise ValueError('include_product_offer_overlap')
        if result['mode']=='SKU级' and (len(overlaps)!=1 or overlaps[0]['offer_id']!=request['offer_id']
                or (overlaps[0]['start'],overlaps[0]['end'])!=(request['start'],request['end'])):
            raise ValueError('include_other_offer_overlap')
    if seen!={(i,m) for i in items for m in ('商品级','SKU级')}:raise ValueError('include_both_offer_modes_required')
    result=read.get('result') or {}
    from campaign_continuous_policy import fingerprint
    if read.get('job_id')!=fingerprint(['discount_readback',request['shop'],result.get('read_request_id')]):
        raise ValueError('include_absence_job_identity_mismatch')
    if (read.get('operation')!='discount_readback' or read.get('state')!='finished'
            or result.get('price_window')!=window or result.get('platform_write') is not False
            or result.get('shop_name')!=request['shop']):raise ValueError('include_absence_read_not_bound')
    raw=load(result['evidence_path'])
    if any(raw.get(k)!=result.get(k) for k in ('rows','shop_name','read_request_id','price_window')):
        raise ValueError('include_original_absence_changed')
    rate=Decimal(request['rate'])
    if rate not in (Decimal('.10'),Decimal('.12'),Decimal('.15')):raise ValueError('include_rate_outside_price_system')
    target_key='medium_target' if rate==Decimal('.10') else 'big_target'
    rows=mapped_erp_rows(snapshot);changes=[];pairs=set()
    if {r['item'] for r in result['rows']}!=items:raise ValueError('include_absence_item_scope_changed')
    protected=a.blocked(request['campaign'],'signup',request['start'],request['end'])
    for r in result['rows']:
        if (r['item'] in protected or r.get('requested_scope_verified') is not True
                or r.get('values') or not r.get('not_enrolled')
                or r['window'].get('offer_id')!=request['offer_id']
                or any(r['window'].get(k)!=v for k,v in window.items())):
            raise ValueError('include_not_proven_missing_or_success_protected')
        for sku in r['not_enrolled']:
            pair=r['item'],sku
            if pair in pairs:raise ValueError('include_duplicate_sku')
            pairs.add(pair)
            match=[e for e in rows if pair[0] in {str(e.get('item')),str(e.get('product_item_id')),*map(str,e.get('product_alt_item_ids') or [])}
                   and sku in {str(e.get('sku')),*map(str,e.get('alt') or [])}]
            if len(match)!=1 or match[0].get('custom') is not False:raise ValueError('include_unique_ordinary_sku_required')
            e=match[0];daily=Decimal(str(e['daily']));target=Decimal(str(e[target_key]));floor=Decimal(str(e['big_target']))
            if not all(v.is_finite() and v>0 for v in (daily,target,floor)):raise ValueError('include_invalid_price')
            deduct=daily-official_cut(daily,rate)-target
            if deduct<0 or deduct!=deduct.quantize(Decimal('.01')) or target<floor:
                raise ValueError('include_frozen_price_assertion_failed')
            changes.append(dict(item=pair[0],sku=sku,offer_id=request['offer_id'],deduct=str(deduct),
                                daily=str(daily),target=str(target),erp_code=e['code']))
    if not 0<len(changes)<=20:raise ValueError('include_missing_scope_too_large')
    offers=[o for o in a.discount_offers() if o.get('platform_offer_id',o['offer_id'])==request['offer_id']
            and (o['start'],o['end'])==(request['start'],request['end'])]
    if len(offers)!=1:raise ValueError('include_registered_parent_offer_not_unique')
    for item in items:
        statuses=[r['status'] for r in offers[0]['items'] if r['item']==item]
        if statuses!=['success']:raise ValueError('include_parent_item_not_confirmed')
    return dict(request=request,rows=changes,campaign=request['campaign'],start=request['start'],end=request['end'],shop=request['shop'])


def prepare(a,request):
    table(a);a.db.execute('BEGIN IMMEDIATE')
    try:
        body=derive(a,request)
        wanted={(r['item'],r['sku'],r['offer_id']) for r in body['rows']}
        for old in a.db.execute('SELECT body FROM missing_discount_includes'):
            if wanted.intersection({(r['item'],r['sku'],r['offer_id']) for r in json.loads(old[0])['rows']}):
                raise ValueError('include_existing_claim_never_replay')
        cid=uuid.uuid4().hex
        a.db.execute('INSERT INTO missing_discount_includes VALUES(?,?,?,NULL,NULL)',(cid,json.dumps(body,ensure_ascii=False),'ready'))
        a.db.execute('COMMIT')
        return dict(claim_id=cid,rows=body['rows'],platform_write=False)
    except BaseException:
        a.db.execute('ROLLBACK');raise


def verify(a,cid,consume_job=None):
    table(a)
    if not re.fullmatch('[0-9a-f]{32}',str(cid)):raise ValueError('include_invalid_claim')
    if consume_job is not None:a.db.execute('BEGIN IMMEDIATE')
    try:
        row=a.db.execute('SELECT * FROM missing_discount_includes WHERE id=?',(cid,)).fetchone()
        if not row or row['state']!='ready':raise ValueError('include_claim_not_fresh_no_replay')
        body=json.loads(row['body'])
        if derive(a,body['request'])!=body:raise ValueError('include_derived_rows_changed')
        if consume_job is not None:
            if not re.fullmatch('[0-9a-f]{64}',consume_job):raise ValueError('include_invalid_job')
            a.db.execute("UPDATE missing_discount_includes SET state='unknown',job_id=? WHERE id=?",(consume_job,cid))
            a.db.execute('COMMIT')
        return dict(body,claim_id=cid,verified_claim=True,dispatch_consumed=consume_job is not None)
    except BaseException:
        if consume_job is not None and a.db.in_transaction:a.db.execute('ROLLBACK')
        raise


def record(a,cid,path):
    table(a);row=a.db.execute('SELECT * FROM missing_discount_includes WHERE id=?',(cid,)).fetchone()
    job=load(path)
    if not row or row['state']!='unknown' or job.get('job_id')!=row['job_id'] or job.get('operation')!='discount_include' or job.get('state')!='finished':
        raise ValueError('include_terminal_not_exact_original_job')
    body=json.loads(row['body']);result=job['result']
    proof=load(result['evidence_path'])
    if any(proof.get(k)!=result.get(k) for k in ('claim_id','rows','state')) or result.get('claim_id')!=cid or result.get('state')!='verified_saved':
        raise ValueError('include_terminal_evidence_changed')
    expected={(r['item'],r['sku']):Decimal(r['deduct']) for r in body['rows']};actual={}
    for r in result['rows']:
        if (r.get('state')!='verified_saved' or r['window'].get('offer_id')!=body['request']['offer_id']
                or any(r['window'].get(k)!=body[k] for k in ('start','end'))):
            raise ValueError('include_actual_window_not_verified')
        for sku,value in r['values'].items():
            pair=r['item'],sku
            if pair in actual:raise ValueError('include_actual_duplicate')
            actual[pair]=Decimal(value)
    if actual!=expected:raise ValueError('include_saved_amount_not_exact')
    ref=dict(path=str(Path(path).resolve()),sha256=file_sha(path),original=dict(path=result['evidence_path'],sha256=file_sha(result['evidence_path'])))
    a.db.execute("UPDATE missing_discount_includes SET state='verified',receipt=? WHERE id=?",(json.dumps(ref),cid))
    return dict(claim_id=cid,verified_skus=len(actual),signup_success=False)


def overlay(a,offers):
    if not a.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='missing_discount_includes'").fetchone():return offers
    result=deepcopy(offers)
    for claim in a.db.execute("SELECT * FROM missing_discount_includes WHERE state IN ('unknown','verified') ORDER BY rowid"):
        body=json.loads(claim['body']);oid=body['request']['offer_id']
        matches=[o for o in result if o.get('platform_offer_id',o['offer_id'])==oid and (o['start'],o['end'])==(body['start'],body['end'])]
        if len(matches)!=1:raise ValueError('include_projection_parent_not_unique')
        offer=matches[0];items={r['item'] for r in body['rows']}
        if claim['state']=='unknown':
            for item in offer['items']:
                if item['item'] in items:item['status']='unknown'
            continue
        ref=json.loads(claim['receipt']);pinned(ref);pinned(ref['original'])
        index={(r['item'],r['sku']):r for r in offer['rows']}
        for row in body['rows']:
            pair=row['item'],row['sku']
            if pair in index:index[pair]['deduct']=row['deduct']
            else:offer['rows'].append(dict(item=row['item'],sku=row['sku'],deduct=row['deduct'],erp_code=row['erp_code']))
    return result


def recover_inputs(transport,payload,exceptions):
    """Requeue only idle input failures now backed by saved inclusion receipts."""
    a=transport.authority
    if not a.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='missing_discount_includes'").fetchone():return {}
    p=payload['identity'];campaign='/'.join(str(p[k]) for k in ('campaign_id','phase_id','sign_record_id'))
    snapshot=a.resolve_snapshot(load(transport.root/'resolved-snapshot.json'))
    mapped=mapped_erp_rows(snapshot);verified={};references={}
    for claim in a.db.execute("SELECT * FROM missing_discount_includes WHERE state='verified'"):
        body=json.loads(claim['body'])
        if (body['campaign'],body['start'],body['end'])!=(campaign,p['start'],p['end']):continue
        if body['request']['price_version']!=snapshot['resolved_price_version_sha256']:continue
        ref=json.loads(claim['receipt']);pinned(ref);pinned(ref['original'])
        for row in body['rows']:
            verified[row['item'],row['sku']]=row
            references[row['item']]=dict(ref,claim_id=claim['id'])
    out={};protected=a.blocked(campaign,'signup',p['start'],p['end'])
    for item,issues in exceptions.items():
        if item in protected or not issues:continue
        valid=True
        for issue in issues:
            pair=item,issue.get('sku')
            if issue.get('reason')=='existing_discount_sku_missing_or_duplicate':
                valid=valid and pair in verified
            elif issue.get('reason')=='erp_mapping_missing_or_not_unique':
                valid=valid and len([r for r in mapped if item in {str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}
                    and pair[1] in {str(r.get('sku')),*map(str,r.get('alt') or [])}])==1
            else:valid=False
        if valid and item in references:out[item]=references[item]
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    x=sub.add_parser('prepare');x.add_argument('--request',required=True)
    x=sub.add_parser('plan');x.add_argument('--request',required=True)
    x=sub.add_parser('verify');x.add_argument('--claim',required=True);x.add_argument('--consume-job')
    x=sub.add_parser('record');x.add_argument('--claim',required=True);x.add_argument('--receipt',required=True)
    args=p.parse_args();a=Authority()
    try:
        out=derive(a,load(args.request)) if args.cmd=='plan' else prepare(a,load(args.request)) if args.cmd=='prepare' else verify(a,args.claim,args.consume_job) if args.cmd=='verify' else record(a,args.claim,args.receipt)
        print(json.dumps(out,ensure_ascii=False))
    finally:a.close()


if __name__=='__main__':main()

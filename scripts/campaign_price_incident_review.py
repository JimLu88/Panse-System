"""Exact 11-item/59-SKU price incident: one read request, guarded local review.

No uploads or platform edits. The existing seller discount surface is not a
complete replacement-price contract; insufficient evidence never emits XLSX.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

from campaign_cap_prepare import coverage, fingerprint, load_checker, pinned, readonly, sha
from campaign_local_offer_projection import IDENTITY, WINDOW, ROOT as TRANSFERS
from campaign_replacement_audit import PROTECTION_ROOT, PROTECTION_FILES, load_current_protection

PROJECT = Path('D:/AI/畔色ERP系统')
OWNER = PROJECT/'outputs/01a067c6-7e83-7483-9a21-84b44ed7299b'
USER = OWNER/'national-closeout-20260929/user-withdrawal-and-relisting.json'
USER_SHA = '548d38c426edbb8c09069a1e52ebd4def543b0b1095b4535d6ab6b69ee6713a1'
TABLES = OWNER/'rotation-20260928/失败后续整批/receipt.json'
TABLES_SHA = '997c035f1f346e8ac4c3eb6e1fa5c2af54b2bcd4bf8feec6c3a4ce47bb62aa90'
OFFERS = {
    '147717819883': ['1035582527998', '1037237545657', '1038071596128', '1046992283533',
                    '1047741358718', '1047742974354', '1047744482178', '1049596868352', '919215369800'],
    '147928680448': ['792992319206', '918692510350'],
}


def source_rows(user, old, tables):
    if (user.get('reported_withdrawals') != OFFERS or user.get('reported_relisted_item') != '1035582527998'
            or user.get('independently_verified') is not False
            or user.get('reupload_old_files_allowed') is not False):
        raise ValueError('exact_current_user_withdrawal_scope_required')
    first = [r for r in old['rows'] if r['item'] in OFFERS['147717819883']]
    second = tables['no_official_rows']
    if (len(first) != 50 or {r['item'] for r in first} != set(OFFERS['147717819883'])
            or len(second) != 9 or {r['item'] for r in second} != set(OFFERS['147928680448'])):
        raise ValueError('exact_50_plus_9_scope_required')
    rows = []
    for group, offer, target_field in ((first, '147717819883', 'frozen_target'), (second, '147928680448', 'target')):
        for r in group:
            target = Decimal(str(r[target_field]))
            if not target.is_finite() or target <= 0 or target != target.quantize(Decimal('.01')):
                raise ValueError('invalid_frozen_target_no_recalculation')
            rows.append(dict(item=r['item'], sku=r['sku'], erp_code=r['erp_code'],
                spec=r.get('spec'), original_offer_id=offer, frozen_target=format(target, '.2f'),
                effective_base=None, replacement_deduction=None))
    if len({(r['item'], r['sku']) for r in rows}) != 59:
        raise ValueError('duplicate_exact_sku_scope')
    return sorted(rows, key=lambda r:(r['item'], r['sku']))


def request_document():
    load_current_protection()  # Validate protected sources; never erase them.
    rows = source_rows(json.loads(pinned(USER, USER_SHA)),
        json.loads(pinned(PROTECTION_ROOT/'prepared-327-receipt.json', PROTECTION_FILES['prepared-327-receipt.json'])),
        json.loads(pinned(TABLES, TABLES_SHA)))
    scope = {}
    for r in rows: scope.setdefault(r['item'], []).append(r['sku'])
    identity = dict(IDENTITY)
    read_id = fingerprint(['price-incident-11-after-user-withdrawal-v1', USER_SHA, rows, WINDOW])
    payload = dict(identity=identity, items=sorted(scope), sku_scope=scope, batch_read=True,
                   price_window=dict(WINDOW), read_request_id=read_id)
    return dict(schema='price-incident-eleven-request-v1', created_at=datetime.now(timezone.utc).isoformat(),
        source=dict(user=dict(path=str(USER), sha256=USER_SHA),
                    original_327=dict(path=str(PROTECTION_ROOT/'prepared-327-receipt.json'), sha256=PROTECTION_FILES['prepared-327-receipt.json']),
                    two_tables=dict(path=str(TABLES), sha256=TABLES_SHA)),
        request=dict(step='discount_item_discovery', payload=payload), rows=rows, sku_count=59, item_count=11,
        relisting=dict(item='1035582527998', user_reported=True, independently_verified=False,
                       previous_user_delisted_exclusion_superseded=True, erp_listing_modified=False),
        original_success_unknown_retained=True, upload_ready=False, platform_write=False)


def verified_request(path):
    raw=Path(path).read_bytes(); doc=json.loads(raw)
    expected=request_document()
    if {k:v for k,v in doc.items() if k!='created_at'} != {k:v for k,v in expected.items() if k!='created_at'}:
        raise ValueError('request_frozen_targets_or_exact_scope_changed')
    date=datetime.fromisoformat(doc['created_at'])
    if date.tzinfo is None:raise ValueError('request_creation_time_unverified')
    return doc,dict(path=str(Path(path).resolve()),sha256=sha(raw))


def write_new(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)


def capture(request_path, receipt_path):
    """03 invokes once. Durable receipt reserves admission before any request."""
    doc, ref=verified_request(request_path)
    record=dict(stage='before_read_admission',request_source=ref,request=doc['request'],platform_write=False)
    write_new(receipt_path,record)  # Existing/unknown receipt cannot silently resubmit.
    sys.path.insert(0,str(PROJECT/'Web-Agent程序'))
    from app.vault.store import get_or_create_api_token
    from campaign_edge_client import EdgeClient
    client=EdgeClient(get_or_create_api_token())
    try:
        job=client.submit(doc['request']['step'],doc['request']['payload'])
        record.update(stage='admitted',job=job)
        Path(receipt_path).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        if job.get('state')=='running':job=client.wait(job['job_id'],timeout=1800)
        record.update(stage='terminal',job=job)
    except Exception as exc:
        record.update(stage='read_blocked_do_not_resubmit',error_type=type(exc).__name__,
                      error_code=getattr(exc,'reason',type(exc).__name__),
                      next_action='Inspect original job/connection or login gate; no restart, new request or platform write.')
    Path(receipt_path).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return dict(stage=record['stage'],job_id=record.get('job',{}).get('job_id'),platform_write=False)


def consume(doc, data, lists, price_surface):
    payload=doc['request']['payload']; requested={(r['item'],r['sku']) for r in doc['rows']}
    surface={}
    for r in price_surface['rows']:
        key=r['item'],r['sku']
        if key in surface or key not in requested:raise ValueError('price_surface_scope_changed')
        surface[key]=r
    if set(surface)!=requested:raise ValueError('price_surface_incomplete_scope')
    rows=[]
    for original in doc['rows']:
        item,sku,old=original['item'],original['sku'],original['original_offer_id']
        evidence=[lists.get((item,m),{}) for m in ('商品级','SKU级')]
        complete=all(e.get('complete') is True for e in evidence)
        seen=[o for e in evidence for o in e.get('offers',[]) if o['offer_id']==old]
        absent=complete and not seen
        # An absent item from BOTH lists proves this snapshot's member absence,
        # not deletion, historical failure or removal from any other offer.
        gaps=[]
        if not complete:gaps.append('withdrawal_lists_incomplete_not_empty')
        elif seen:gaps.append('original_offer_member_still_displayed')
        observed=[o for e in evidence for o in e.get('offers',[]) if o['start']<=WINDOW['end'] and WINDOW['start']<=o['end']]
        if observed:gaps.append('remaining_overlapping_offer_requires_exact_sku_review')
        gaps.append(surface[item,sku]['gap'])
        rows.append(dict(original,withdrawal_member_absent_verified=absent,
            original_offer_still_displayed=bool(seen),other_overlap_offer_ids=sorted({o['offer_id'] for o in observed}),
            price_surface=surface[item,sku],gaps=gaps,upload_ready=False))
    return dict(schema='price-incident-eleven-review-v1',rows=rows,items=11,skus=59,
        counts=dict(verified_withdrawal_skus=sum(r['withdrawal_member_absent_verified'] for r in rows),
                    verified_complete_price_compositions=0,upload_ready_skus=0),
        gaps=dict(Counter(g for r in rows for g in r['gaps'])),
        generation_state='blocked_complete_effective_price_composition_not_verified',files=[],
        required_next_evidence='One batch of exact SKU effective price components/order/threshold rules on a documented official surface; current discount editor alone is insufficient.',
        original_success_unknown_retained=True,old_327_and_two_tables_9_files_not_reused=True,
        other_eight_item_holds_unchanged=True,relisting=doc['relisting'],
        business_acceptance=False,platform_write=False,database_write=False,upload_ready=False)


def review(request_path, job_id):
    doc, ref=verified_request(request_path); payload=doc['request']['payload']
    expected_id=fingerprint(['discount_item_discovery',payload['identity']['shop_name'],payload['read_request_id']])
    if job_id!=expected_id:raise ValueError('exact_new_incident_read_required_no_old_snapshot_reuse')
    db=readonly(TRANSFERS/'jobs.sqlite')
    try:
        db.execute('BEGIN')
        row=db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(job_id,)).fetchone()
        if not row or row['operation']!='discount_item_discovery' or row['state']!='finished':
            raise ValueError('read_not_finished_report_original_job_blocker_no_retry')
        req=json.loads(row['request']);durable=json.loads(row['result'] or '{}')
        if (req.get('payload')!=payload or req.get('action_id')!=job_id
                or req.get('request_sha')!=fingerprint(payload)
                or row['request_sha']!=fingerprint(dict(operation=row['operation'],request=req))
                or datetime.fromisoformat(row['updated_at'])<datetime.fromisoformat(doc['created_at'])):
            raise ValueError('read_request_identity_or_time_changed')
        path=TRANSFERS/job_id/'discount-batch-read.json';raw=path.read_bytes();data=json.loads(raw)
        if (data.get('platform_write') is not False or data.get('state') not in ('readback','partial_readback')
                or any(data.get(k)!=payload[k] for k in ('items','sku_scope','price_window','read_request_id'))
                or any(durable.get(k)!=v for k,v in data.items())
                or Path(durable.get('evidence_path','')).resolve()!=path.resolve()):
            raise ValueError('read_file_not_bound_to_original_job')
        # Recompute from raw rows, never trust a caller's composition_ready flag.
        sys.path.insert(0,str(PROJECT/'Web-Agent程序'))
        from app.engine.campaign_discount_price_surface import summarize
        result=consume(doc,data,coverage(data,payload,load_checker()),summarize(payload,data['rows']))
        result.update(request_source=ref,terminal=dict(job_id=job_id,finished_at=row['updated_at'],
            path=str(path),sha256=sha(raw),snapshot_only=True),read_issues=data.get('issues',[]))
        return result
    finally:db.close()


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__); sub=ap.add_subparsers(dest='command',required=True)
    prepare=sub.add_parser('prepare');prepare.add_argument('--output',type=Path,required=True)
    read=sub.add_parser('capture');read.add_argument('--request',type=Path,required=True);read.add_argument('--receipt',type=Path,required=True)
    inspect=sub.add_parser('review');inspect.add_argument('--request',type=Path,required=True)
    inspect.add_argument('--job-id',required=True);inspect.add_argument('--output',type=Path,required=True)
    args=ap.parse_args(argv)
    if args.command=='capture':result=capture(args.request,args.receipt)
    else:
        if args.output.exists():raise ValueError('output_exists_do_not_overwrite')
        result=request_document() if args.command=='prepare' else review(args.request,args.job_id)
        write_new(args.output,result)
    print(json.dumps({k:result[k] for k in ('schema','stage','job_id','item_count','sku_count','counts','generation_state','upload_ready','platform_write') if k in result},ensure_ascii=False))


if __name__=='__main__':main()

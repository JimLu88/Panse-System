"""Scoped current offer availability; never erase import success/unknown history.

Only the caller's NEW exact window can consume a fixed readback. A missing
offer is not labelled deleted. It can cease blocking only when the same
request carries the user's existing removal instruction and the fixed query
has verified an unfiltered exact-ID absence.
"""
import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TRANSFER_ROOT=Path('D:/AI/畔色ERP系统/Web-Agent程序/data/output/campaign-transfers')


def _verified_navigation_recovery(root,job_id,result):
    """Accept only the same-job recorded pre-read recovery, never a loose subpath."""
    folder=(root/job_id).resolve()
    proof=(folder/'read-recovery-1'/'discount-readback.json').resolve()
    if Path(result.get('evidence_path','')).resolve()!=proof:
        raise ValueError('offer_availability_readback_changed')
    try:
        db=sqlite3.connect((root/'jobs.sqlite').as_uri()+'?mode=ro',uri=True)
        try:row=db.execute('SELECT previous_result FROM campaign_offer_availability_navigation_recovery WHERE id=?',(job_id,)).fetchone()
        finally:db.close()
    except sqlite3.Error as exc:
        raise ValueError('offer_availability_recovery_audit_missing') from exc
    if not row:raise ValueError('offer_availability_recovery_audit_missing')
    old=json.loads(row[0]);previous=old.get('recording') or {};current=result.get('recording') or {}
    old_video=Path(previous.get('video') or '').resolve()
    video=Path(current.get('video') or '').resolve()
    failure=(folder/'failure-disposition.json').resolve()
    try:disposition=json.loads(failure.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise ValueError('offer_availability_recovery_audit_changed') from exc
    if (old.get('error')!='TimeoutError' or old.get('automatic_retry') is not False
            or not any(f.get('file')=='campaign_bound_transfers.py' and f.get('function')=='execute'
                       and f.get('line')==352 for f in old.get('program_location',[]))
            or previous.get('events')!=0 or old.get('failure_disposition_path')!=str(failure)
            or disposition.get('job_id')!=job_id or disposition.get('operation')!='discount_readback'
            or disposition.get('disposition',{}).get('action')!='manual_program_repair'
            or not old_video.is_file() or not old_video.is_relative_to(folder/'recording')
            or hashlib.sha256(old_video.read_bytes()).hexdigest()!=previous.get('video_sha256')
            or current.get('active') is not False or not current.get('frames')
            or current.get('capture_errors')!=0 or current.get('error')
            or not video.is_file() or not video.is_relative_to(folder/'read-recovery-1'/'recording')
            or hashlib.sha256(video.read_bytes()).hexdigest()!=current.get('video_sha256')):
        raise ValueError('offer_availability_recovery_audit_changed')


def verified_scope(ref, *, now=None):
    from campaign_entry_authority import load,file_sha
    from campaign_continuous_policy import fingerprint
    path=Path(ref['path']).resolve(strict=True)
    if file_sha(path)!=ref['sha256']:raise ValueError('offer_availability_receipt_changed')
    doc=load(path)
    if doc.get('schema')!='campaign_discount_availability_v1':raise ValueError('offer_availability_schema')
    request_path=Path(doc['request_path']).resolve(strict=True)
    request=load(request_path)
    if fingerprint(request)!=doc['controller_id']:raise ValueError('offer_availability_original_request_changed')
    # Scope comes from the persisted request, not a price window invented by
    # this receipt. The user confirmation cannot grant another event/window.
    from campaign_segmented_time import build_plan
    candidates=build_plan(dict(request['calendar'],price_version='availability_scope_only'))['segments']
    scope=doc['scope']
    if not any(s.get('campaign')==scope['campaign'] and s.get('shop_id')==scope['shop'] and all(s.get('price_window',{}).get(k)==scope[k] for k in ('start','end')) for s in candidates):
        raise ValueError('offer_availability_target_window_not_in_original_request')
    job_id=doc['job_id'];root=TRANSFER_ROOT.resolve()
    db=sqlite3.connect((root/'jobs.sqlite').as_uri()+'?mode=ro',uri=True)
    try:row=db.execute('SELECT operation,state,request,result FROM campaign_transfer_jobs WHERE id=?',(job_id,)).fetchone()
    finally:db.close()
    if not row or row[:2]!=('discount_readback','finished'):raise ValueError('offer_availability_finished_fixed_read_required')
    req,result=json.loads(row[2]),json.loads(row[3]);payload=req['payload']
    identity=payload['identity']
    if fingerprint(['discount_readback',identity['shop_name'],payload['read_request_id']])!=job_id:
        raise ValueError('offer_availability_job_identity_changed')
    original=request.get('pages',{}).get(scope['campaign'],{})
    if (not original or any(identity.get(k)!=original.get(k) for k in
            ('campaign_id','phase_id','sign_record_id','title','phase_title','start','end'))
            or identity['shop_name']!=original.get('shop_id')):raise ValueError('offer_availability_original_activity_changed')
    if (req['request_sha']!=fingerprint(payload) or payload.get('offer_status_only') is not True
            or result.get('state')!='offer_status_readback' or result.get('platform_write') is not False
            or scope['campaign']!='/'.join(identity[k] for k in ('campaign_id','phase_id','sign_record_id'))
            or result.get('shop_name')!=scope['shop']):raise ValueError('offer_availability_read_scope_changed')
    proof=Path(result['evidence_path']).resolve(strict=True)
    if proof==(root/job_id/'read-recovery-1'/'discount-readback.json').resolve():
        _verified_navigation_recovery(root,job_id,result)
    elif proof!=(root/job_id/'discount-readback.json').resolve():
        raise ValueError('offer_availability_readback_changed')
    if file_sha(proof)!=doc['readback_sha256']:
        raise ValueError('offer_availability_readback_changed')
    saved=load(proof)
    if any(saved.get(k)!=result.get(k) for k in saved):raise ValueError('offer_availability_result_mismatch')
    rows=saved['rows'];requested={r['offer_id'] for r in payload['offers']}
    if len(rows)!=len(requested) or {r['offer_id'] for r in rows}!=requested:raise ValueError('offer_availability_incomplete_read')
    if doc.get('observed_at') is not None and doc['observed_at']!=max(r['observed_at'] for r in rows):
        raise ValueError('offer_availability_order_evidence_changed')
    accepted=[];now=now or datetime.now(timezone.utc)
    for row in rows:
        age=(now-datetime.fromisoformat(row['observed_at'])).total_seconds()
        if not 0<=age<=1800:raise ValueError('offer_availability_readback_stale')
        paused=row.get('observed_state')=='paused' and row.get('window',{}).get('offer_id')==row['offer_id']
        absent=(row.get('observed_state')=='not_found' and row.get('unfiltered_exact_query') is True
                and row.get('search_value')==row['offer_id'] and doc.get('user_removed_old_offers') is True)
        if (paused or absent) and row.get('platform_write') is False:accepted.append(row['offer_id'])
    if sorted(accepted)!=sorted(doc['offer_ids']):raise ValueError('offer_availability_not_proven')
    return dict(scope,offer_ids=accepted,requested_offer_ids=sorted(requested),
                observed_at=max(r['observed_at'] for r in rows),old_window=payload['price_window'])


def overlay(authority, offers):
    sources=[s for s in authority.sources() if s['kind']=='discount_availability']
    # The newest verified exact-ID status must be considered before older
    # immutable receipts whose 30-minute window has expired.
    sources.sort(key=lambda s:s['document'].get('observed_at',''),reverse=True)
    refs=[dict(path=s['path'],sha256=s['sha256']) for s in sources]
    for offer in offers:
        if refs:offer['availability_refs']=refs
    return offers


def inactive_for_window(offer,campaign,start,end):
    latest=None;stale=False
    platform_id=offer.get('platform_offer_id',offer['offer_id'])
    for ref in offer.get('availability_refs',[]):
        from campaign_entry_authority import load,file_sha
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('offer_availability_receipt_changed')
        document=load(ref['path']);target=document['scope']
        if (campaign,start,end)!=(target['campaign'],target['start'],target['end']):continue
        try:scope=verified_scope(ref)
        except ValueError as exc:
            if str(exc)!='offer_availability_readback_stale':raise
            if platform_id in document.get('offer_ids',[]):stale=True
            continue
        if ((campaign,start,end)!=(scope['campaign'],scope['start'],scope['end'])
                or (start,end)==(offer['start'],offer['end'])):continue
        if platform_id not in scope['requested_offer_ids']:continue
        inactive=((offer['start'],offer['end'])==(scope['old_window']['start'],scope['old_window']['end'])
                  and platform_id in scope['offer_ids'])
        observed=datetime.fromisoformat(scope['observed_at'])
        if latest is None or observed>latest[0]:latest=(observed,inactive)
    if latest is not None:return latest[1]
    if stale:raise ValueError('offer_availability_readback_stale')
    return False

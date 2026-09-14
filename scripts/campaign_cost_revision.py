"""One explicitly requested cost-version migration, not a tolerance exception.

No browser/ERP pricing writes. The recorded discount_reprice operation is the
only transport. This grant cannot be supplied or expanded by a job caller.
"""
from decimal import Decimal
import argparse
import json
from pathlib import Path
import re
import sys

from campaign_entry_authority import Authority, file_sha, load
from campaign_price_snapshot import digest
from campaign_generate_current_files import build_rows
from campaign_continuous_policy import fingerprint

OUT = Path('D:/AI/畔色ERP系统/outputs/01a067c6-7e83-7483-9a21-84b44ed7299b')
READ = Path('D:/AI/畔色ERP系统/Web-Agent程序/data/output/campaign-transfers/71a2fe2ea36f01a97c39bb379d9ec94a547ea0aef08c04328b7c8bf7275d749c/verified-membership-reuse.json')
GRANT = 'rocktable-current-cost-20260914-v1'
ITEM = '792992319206'
OFFER = '144881379873'
CAMPAIGN = 'legacy/itemApply/3172207691'
WINDOW = {'start':'2026-09-14 00:00:00','end':'2026-09-16 19:59:59'}
VERSION = 'c82cfbec6fa90f4fcbedf6ee7be52af25efaf67c986d8fae29fdda31f16b4bdd'
SKUS = tuple(str(s) for s in range(6126676768459,6126676768464))
EXCLUDED = {'5602711422165','6056644376634'}
RATE, TARGET = Decimal('.1'), 'medium'
SOURCES = [
    {'path':str(OUT/'rocktable-current-user-direction-20260914.json'),'sha256':'aec0475b479b87a6f7166c2d95222933b359f6d3e7db1da48c7b873ac87bb8e4'},
    {'path':str(OUT/'rotation-7770-current-price-snapshot-20260914.json'),'sha256':'85b1514a15d5c62dcc20acfc98c21968166988ab7d40070178218fa9a6ab0d30'},
    {'path':str(READ),'sha256':'06fbfcdd800c9769794425902e1f304957deec14eec06041adc87c2dff8eaf7c'},
]


def table(a):
    a.db.execute('''CREATE TABLE IF NOT EXISTS campaign_cost_revisions(
      id TEXT PRIMARY KEY, grant_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL,
      state TEXT NOT NULL, job_id TEXT, receipt TEXT)''')


def engine(grant_id):
    if grant_id == GRANT:return sys.modules[__name__]
    from campaign_autumn_cost_revision import GRANT as autumn_grant
    if grant_id == autumn_grant:
        import campaign_autumn_cost_revision
        return campaign_autumn_cost_revision
    raise ValueError('unknown_explicit_cost_revision_grant')


def calculate(snapshot, read, *, policy=None):
    """Same frozen generator, complete ordinary scope, no custom discounts."""
    p=policy or sys.modules[__name__]
    if snapshot['resolved_price_version_sha256'] != p.VERSION:
        raise ValueError('cost_revision_price_version_changed')
    if (read.get('state')!='readback' or read.get('platform_write') is not False
            or read.get('shop_name')!='畔色木作' or read.get('price_window')!=p.WINDOW
            or len(read.get('rows',[]))!=1):
        raise ValueError('cost_revision_exact_read_required')
    group=read['rows'][0]
    if (group['item']!=p.ITEM or group['window'].get('offer_id')!=p.OFFER
            or any(group['window'].get(k)!=v for k,v in p.WINDOW.items())
            or set(group['values'])!=set(p.SKUS) or set(group.get('not_enrolled',[]))!=p.EXCLUDED
            or group.get('requested_scope_verified') is not True):
        raise ValueError('cost_revision_read_scope_changed')
    identities=[{'item':p.ITEM,'sku':s,'state':''} for s in p.SKUS]
    activity, discounts, issues=build_rows(snapshot,identities,p.RATE,p.TARGET,{})
    if issues or len(activity)!=5 or len(discounts)!=5 or any(r['custom'] for r in activity):
        raise ValueError('cost_revision_generator_scope_or_formula_failed')
    rows=[]
    for row in discounts:
        old=Decimal(group['values'][row['sku']])
        if not old.is_finite() or old<=0 or old!=old.quantize(Decimal('.01')):
            raise ValueError('cost_revision_invalid_old_amount')
        rows.append(dict(row,offer_id=p.OFFER,old_deduct=str(old),new_deduct=row['deduct']))
    return rows


def derive():
    for ref in SOURCES:
        if file_sha(ref['path'])!=ref['sha256']:
            raise ValueError('cost_revision_pinned_source_changed')
    direction,snapshot,read=[load(r['path']) for r in SOURCES]
    if direction.get('resolved_price_version_sha256')!=VERSION:
        raise ValueError('cost_revision_direction_version_changed')
    if file_sha(read['source_evidence_path'])!=read['source_sha256']:
        raise ValueError('cost_revision_recorded_source_changed')
    recording=read['recording']
    if recording.get('frames',0)<1 or file_sha(recording['video'])!=recording['video_sha256']:
        raise ValueError('cost_revision_recording_changed')
    return dict(grant_id=GRANT,campaign=CAMPAIGN,shop_name='畔色木作',**WINDOW,
                price_version=VERSION,rows=calculate(snapshot,read),sources=SOURCES,
                automatic_retry=False,rotation_performed=False)


def check_current(a,body,*,policy=None):
    p=policy or sys.modules[__name__]
    check_same_segment_failures(a,policy=p)
    # Prior successful enrollment is never unblocked here. Only its explicitly
    # authorized existing discount is amended; unknown enrollment still blocks.
    blocked=a.blocked(p.CAMPAIGN,'signup',**p.WINDOW)
    if blocked.get(p.ITEM)=='unknown':raise ValueError('cost_revision_unknown_signup_protected')
    offers=a.discount_offers()
    matching=[o for o in offers if o.get('platform_offer_id',o['offer_id'])==p.OFFER
              and (o['start'],o['end'])==(p.WINDOW['start'],p.WINDOW['end'])]
    if len(matching)!=1:raise ValueError('cost_revision_registered_offer_not_unique')
    offer=matching[0]
    if not any(i['item']==p.ITEM and i['status']=='success' for i in offer['items']):
        raise ValueError('cost_revision_existing_offer_not_verified')
    for other in offers:
        if other is offer:continue
        if other['start']<=p.WINDOW['end'] and p.WINDOW['start']<=other['end'] and any(
                i['item']==p.ITEM and i['status'] in ('success','unknown') for i in other['items']):
            raise ValueError('cost_revision_overlapping_offer')
    for row in body['rows']:
        saved=[r for r in offer['rows'] if (r['item'],r['sku'])==(p.ITEM,row['sku'])]
        if len(saved)!=1 or Decimal(saved[0]['deduct'])!=Decimal(row['old_deduct']):
            raise ValueError('cost_revision_registered_old_amount_changed')
    exists=a.db.execute("SELECT 1 FROM sqlite_master WHERE name='continuous_discount_repairs'").fetchone()
    if exists:
        for r in a.db.execute("SELECT body FROM continuous_discount_repairs WHERE state IN ('claimed_not_dispatched','dispatched_unknown')"):
            pending=json.loads(r['body'])
            if pending['start']<=p.WINDOW['end'] and p.WINDOW['start']<=pending['end'] and any(
                    x['item']==p.ITEM for x in pending['rows']):
                raise ValueError('cost_revision_pending_amendment_protected')


def check_same_segment_failures(a,*,policy=None):
    """Reuse existing reports, not a browser preflight or historical blacklist.

    Super-reduce's three-year official validity is not a price segment. A prior
    run for another segment is risk evidence, not this segment's new result.
    """
    p=policy or sys.modules[__name__]
    if not a.db.execute("SELECT 1 FROM sqlite_master WHERE name='attempts'").fetchone():return
    records=a.db.execute('''SELECT a.id,a.evidence,b.body FROM attempts a
      JOIN bundles b ON b.id=a.bundle_id WHERE a.campaign=? AND a.phase='signup'
      AND a.item=? AND a.status='failed' ''',(p.CAMPAIGN,p.ITEM))
    for row in records:
        bundle=json.loads(row['body'])
        if any(bundle.get(k)!=v for k,v in p.WINDOW.items()):continue
        if not row['evidence']:raise ValueError('cost_revision_same_segment_failure_evidence_missing')
        ref=json.loads(row['evidence'])
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('cost_revision_failure_evidence_changed')
        terminal=load(ref['path'])
        if (terminal.get('claim_id')!=row['id'].split(':')[0] or terminal.get('campaign')!=p.CAMPAIGN
                or terminal.get('phase')!='signup' or terminal.get('terminal') is not True
                or not any(i['item']==p.ITEM and i['status']=='failed' for i in terminal.get('items',[]))):
            raise ValueError('cost_revision_failure_identity_changed')
        if any(e.get('item')==p.ITEM and e.get('kind')=='no_sales' for e in terminal.get('errors',[])):
            feedback=terminal['feedback']
            if file_sha(feedback['path'])!=feedback['sha256']:
                raise ValueError('cost_revision_failure_report_changed')
            raise ValueError('cost_revision_same_segment_no_sales_skip')


def claim(a,*,policy=None):
    p=policy or sys.modules[__name__]
    table(a);body=p.derive();cid=digest(body)[:32]
    a.db.execute('BEGIN IMMEDIATE')
    try:
        existing=a.db.execute('SELECT * FROM campaign_cost_revisions WHERE grant_id=?',(p.GRANT,)).fetchone()
        if existing:
            if existing['id']!=cid or existing['body']!=json.dumps(body,ensure_ascii=False,sort_keys=True):
                raise ValueError('cost_revision_grant_already_bound')
            if existing['state']!='claimed_not_dispatched':
                raise ValueError('cost_revision_already_dispatched_do_not_replay')
        p.check_current(a,body)
        if not existing:a.db.execute('INSERT INTO campaign_cost_revisions VALUES(?,?,?,?,NULL,NULL)',
            (cid,p.GRANT,json.dumps(body,ensure_ascii=False,sort_keys=True),'claimed_not_dispatched'))
        a.db.execute('COMMIT')
        return dict(body,claim_id=cid,platform_write=False)
    except BaseException:
        if a.db.in_transaction:a.db.execute('ROLLBACK')
        raise


def verify(a,cid,*,consume_job=None):
    table(a)
    if not re.fullmatch('[0-9a-f]{32}',str(cid)):raise ValueError('invalid_cost_claim')
    if consume_job is not None:
        if consume_job!=fingerprint(['discount_reprice',cid]):raise ValueError('invalid_cost_job')
        a.db.execute('BEGIN IMMEDIATE')
    try:
        row=a.db.execute('SELECT * FROM campaign_cost_revisions WHERE id=?',(cid,)).fetchone()
        if not row or row['state']!='claimed_not_dispatched':
            raise ValueError('cost_revision_not_fresh_do_not_replay')
        p=engine(row['grant_id']);body=p.derive()
        if json.loads(row['body'])!=body or digest(body)[:32]!=cid:
            raise ValueError('cost_revision_claim_changed')
        p.check_current(a,body)
        if consume_job is not None:
            a.db.execute("UPDATE campaign_cost_revisions SET state='dispatched_unknown',job_id=? WHERE id=?",(consume_job,cid))
            a.db.execute('COMMIT')
        return dict(body,claim_id=cid,verified_claim=True,dispatch_consumed=consume_job is not None,job_id=consume_job)
    except BaseException:
        if consume_job is not None and a.db.in_transaction:a.db.execute('ROLLBACK')
        raise


def validate_proof(body,cid,jid,proof,*,policy=None):
    p=policy or sys.modules[__name__]
    if proof.get('state')!='verified_saved' or proof.get('claim_id')!=cid or proof.get('job_id')!=jid:
        raise ValueError('cost_revision_saved_proof_missing')
    groups=proof.get('rows',[])
    if len(groups)!=1:raise ValueError('cost_revision_saved_scope_mismatch')
    group=groups[0]
    if (group.get('item')!=p.ITEM or group.get('offer_id')!=p.OFFER or group.get('state')!='verified_saved'
            or group.get('window',{}).get('offer_id')!=p.OFFER
            or any(group.get('window',{}).get(k)!=v for k,v in p.WINDOW.items())
            or set(group.get('values',{}))!=set(p.SKUS) or set(group.get('before',{}))!=set(p.SKUS)):
        raise ValueError('cost_revision_saved_scope_mismatch')
    for r in body['rows']:
        if (Decimal(group['values'][r['sku']])!=Decimal(r['new_deduct'])
                or Decimal(group['before'][r['sku']])!=Decimal(r['old_deduct'])):
            raise ValueError('cost_revision_saved_amount_mismatch')


def record(a,cid,job):
    table(a);row=a.db.execute('SELECT * FROM campaign_cost_revisions WHERE id=?',(cid,)).fetchone()
    if (not row or row['state'] not in ('dispatched_unknown','verified')
            or job.get('operation')!='discount_reprice' or job.get('state')!='finished'
            or row['job_id']!=job.get('job_id')):
        raise ValueError('cost_revision_job_binding_mismatch')
    p=engine(row['grant_id']);body=p.derive()
    if body!=json.loads(row['body']):raise ValueError('cost_revision_claim_changed')
    result=job['result'];proof=load(result['evidence_path'])
    if any(proof.get(k)!=result.get(k) for k in ('state','claim_id','job_id','rows')):
        raise ValueError('cost_revision_saved_proof_changed')
    p.validate_proof(body,cid,row['job_id'],proof)
    rec=result['recording']
    if rec.get('frames',0)<1 or file_sha(rec['video'])!=rec['video_sha256']:
        raise ValueError('cost_revision_save_recording_missing')
    ref={'path':result['evidence_path'],'sha256':file_sha(result['evidence_path'])}
    if row['state']=='verified' and json.loads(row['receipt'])!=ref:
        raise ValueError('cost_revision_receipt_immutable')
    a.db.execute("UPDATE campaign_cost_revisions SET state='verified',receipt=? WHERE id=?",(json.dumps(ref),cid))
    return dict(state='verified_discount_only',claim_id=cid,rows=5,signup_completed=False,receipt=ref)


def overlay(a,offers):
    if not a.db.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_cost_revisions'").fetchone():return offers
    # Saved outcome unknown blocks other generators from reusing old amounts.
    # This does not erase the original successful offer evidence or receipt.
    for row in a.db.execute("SELECT body FROM campaign_cost_revisions WHERE state='dispatched_unknown'"):
        body=json.loads(row['body'])
        for offer in offers:
            if offer.get('platform_offer_id',offer['offer_id']) in {r['offer_id'] for r in body['rows']} and all(offer[k]==body[k] for k in ('start','end')):
                for item in offer['items']:
                    if item['item'] in {r['item'] for r in body['rows']}:item['status']='unknown'
                offer['cost_revision_pending']=True
    for row in a.db.execute("SELECT * FROM campaign_cost_revisions WHERE state='verified'"):
        p=engine(row['grant_id']);body=p.derive()
        if json.loads(row['body'])!=body:raise ValueError('cost_revision_overlay_claim_changed')
        ref=json.loads(row['receipt'])
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('cost_revision_receipt_changed')
        p.validate_proof(body,row['id'],row['job_id'],load(ref['path']))
        for change in body['rows']:
            candidates=[r for o in offers if o.get('platform_offer_id',o['offer_id'])==p.OFFER
                and (o['start'],o['end'])==(p.WINDOW['start'],p.WINDOW['end']) for r in o['rows']
                if (r['item'],r['sku'])==(p.ITEM,change['sku'])]
            if len(candidates)!=1 or Decimal(candidates[0]['deduct'])!=Decimal(change['old_deduct']):
                raise ValueError('cost_revision_overlay_old_conflict')
            candidates[0].update(deduct=change['new_deduct'],amendment_receipt=ref,
                                 cost_price_version=p.VERSION)
    return offers


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','claim','verify','record'])
    p.add_argument('--claim');p.add_argument('--consume-job');p.add_argument('--job-file')
    args=p.parse_args();a=Authority()
    try:
        if args.action=='plan':result=dict(derive(),platform_write=False,claim_created=False)
        elif args.action=='claim':result=claim(a)
        elif args.action=='verify':result=verify(a,args.claim,consume_job=args.consume_job)
        else:result=record(a,args.claim,load(args.job_file))
        print(json.dumps(result,ensure_ascii=False))
    finally:a.close()

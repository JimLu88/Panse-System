"""Reconcile saved prefix; dispatch only proven never-attempted remaining rows."""
from collections import OrderedDict
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import uuid


def partition(body, observed, completed_keys, save_count):
    groups=OrderedDict()
    for row in body['rows']:
        groups.setdefault((row['offer_id'],row['item']),[]).append(row)
    keys=list(groups)
    if completed_keys!=keys[:len(completed_keys)] or save_count!=len(completed_keys)+1 or save_count>=len(keys):
        raise ValueError('amend_attempted_prefix_not_proven')
    actual={(r['window']['offer_id'],r['item']):r for r in observed}
    if len(actual)!=len(observed) or set(actual)!=set(groups):raise ValueError('amend_read_scope_changed')
    applied=[];remaining=[]
    for index,key in enumerate(keys):
        row=actual[key];wanted=groups[key]
        if any(row['window'][k]!=body[k] for k in ('start','end')):
            raise ValueError('amend_read_window_changed')
        if set(row['values'])!={r['sku'] for r in wanted} or row.get('not_enrolled'):
            raise ValueError('amend_read_sku_scope_changed')
        field='new_deduct' if index<save_count else 'old_deduct'
        if any(Decimal(str(row['values'][r['sku']]))!=Decimal(str(r[field])) for r in wanted):
            raise ValueError('amend_actual_amount_not_expected_'+field)
        if index<save_count:applied.append(dict(row,offer_id=key[0]))
        else:remaining.extend(wanted)
    return applied,remaining


def run(source_id,read_id,edge,*,dry_run=False):
    from campaign_entry_authority import Authority,load,file_sha
    from campaign_continuous_policy import fingerprint
    from campaign_continuous_transport import persist
    from campaign_continuous_repairs import verify_claim,reconcile_finished_amend
    root=Path('D:/AI/畔色ERP系统/Web-Agent程序/data/output/campaign-transfers')
    db=sqlite3.connect(root/'jobs.sqlite',isolation_level=None);db.row_factory=sqlite3.Row
    authority=Authority()
    try:
        source=db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(source_id,)).fetchone()
        read=db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(read_id,)).fetchone()
        if not source or source['operation']!='discount_amend' or source['state']!='unknown':
            raise ValueError('original_unknown_amend_required')
        req=load_json(source['request']);old=load_json(source['result']);rreq=load_json(read['request']);result=load_json(read['result'])
        if (old.get('reason')!='surface_not_unique:2'
                or not any(f.get('file')=='campaign_discount_page.py' and f.get('function')=='amend_once' and f.get('line')==395 for f in old.get('program_location',[]))
                or read['operation']!='discount_readback' or read['state']!='finished'
                or result.get('state')!='readback' or result.get('platform_write') is not False
                or rreq['payload']['read_request_id']!=fingerprint(['actual-amend-reconcile',source_id,fingerprint(old)])
                or req['payload']['identity']!=rreq['payload']['identity']
                or source['request_sha']!=fingerprint({'operation':'discount_amend','request':req})
                or read['request_sha']!=fingerprint({'operation':'discount_readback','request':rreq})
                or req['request_sha']!=fingerprint(req['payload']) or rreq['request_sha']!=fingerprint(rreq['payload'])):
            raise ValueError('original_amend_read_binding_changed')
        cid=req['payload']['claim_id']
        if source_id!=fingerprint(['discount_amend',cid]) or req.get('action_id')!=source_id:
            raise ValueError('original_amend_id_changed')
        claim=authority.db.execute('SELECT * FROM continuous_discount_repairs WHERE id=?',(cid,)).fetchone()
        if claim['state']!='dispatched_unknown' or claim['job_id']!=source_id:
            raise ValueError('original_amend_claim_not_dispatched')
        body=load_json(claim['body'])
        for ref in body['sources']:
            if file_sha(ref['path'])!=ref['sha256']:raise ValueError('original_repair_source_changed')
        evidence=Path(result['evidence_path']).resolve(strict=True)
        if not evidence.is_relative_to((root/read_id).resolve()):raise ValueError('actual_readback_path_changed')
        if load(evidence)!={k:v for k,v in result.items() if k!='evidence_path' and k!='recording'}:
            # Recorder appends metadata to the returned envelope, not raw readback.
            raw=load(evidence)
            if any(raw.get(k)!=result.get(k) for k in ('state','rows','shop_name','read_request_id','price_window','platform_write')):
                raise ValueError('actual_readback_evidence_changed')
        record=old['recording'];video=Path(record['video']).resolve(strict=True)
        if not video.is_relative_to((root/source_id).resolve()) or file_sha(video)!=record['video_sha256']:
            raise ValueError('original_recording_changed')
        events=load(video.parent/'recording.json')['events']
        saves=sum(e.get('kind')=='click' and e.get('label')=='确认修改' for e in events)
        clicks=[e.get('label') for e in events if e.get('kind')=='click']
        if not clicks or clicks[-1]!='确认修改':raise ValueError('post_save_read_failure_not_proven')
        completed=[]
        for key in dict.fromkeys((r['offer_id'],r['item']) for r in body['rows']):
            p=root/source_id/f'amended-{key[0]}-{key[1]}.json'
            if p.exists():
                item=load(p)
                if item.get('state')!='verified_saved' or item.get('item')!=key[1]:raise ValueError('original_saved_prefix_changed')
                completed.append(key)
        applied,remaining=partition(body,result['rows'],completed,saves)
        if dry_run:
            return {'verified_saved_products':len(applied),'never_attempted_products':len({r['item'] for r in remaining}),
                    'never_attempted_skus':len(remaining),'platform_write':False}
        folder=root/source_id/'partial-reconciliation';folder.mkdir(exist_ok=True)
        plan_path=folder/'plan.json'
        basis={'source_id':source_id,'read_id':read_id,'source_result_sha':fingerprint(old),
               'read_evidence':{'path':str(evidence),'sha256':file_sha(evidence)},'original_claim_sha':fingerprint(body),
               'applied':applied,'remaining':remaining}
        if plan_path.exists():
            plan=load(plan_path)
            if any(plan.get(k)!=v for k,v in basis.items()):raise ValueError('recovery_plan_changed')
        else:
            if db.execute("SELECT 1 FROM campaign_transfer_jobs WHERE operation IN ('discount','discount_amend','discount_reprice','signup') AND updated_at>? AND id<>? LIMIT 1",(read['updated_at'],source_id)).fetchone():
                raise ValueError('later_write_requires_fresh_read')
            plan=dict(basis,child_claim_id=uuid.uuid4().hex)
            persist(plan_path,plan)
        child=plan['child_claim_id'];child_body=dict(body,rows=remaining,sources=body['sources']+[basis['read_evidence']])
        previous=authority.db.execute('SELECT * FROM continuous_discount_repairs WHERE id=?',(child,)).fetchone()
        if previous is None:
            authority.db.execute('INSERT INTO continuous_discount_repairs(id,body,state) VALUES(?,?,?)',
                (child,json.dumps(child_body,ensure_ascii=False),'claimed_not_dispatched'))
            verify_claim(authority,child)
        elif load_json(previous['body'])!=child_body:raise ValueError('child_claim_changed')
        child_id=fingerprint(['discount_amend',child])
        existing=db.execute('SELECT state FROM campaign_transfer_jobs WHERE id=?',(child_id,)).fetchone()
        if existing is None:
            job=edge.submit('discount_amend',dict(req['payload'],claim_id=child))
        else:job=edge.status(child_id)
        if job['state']=='running':job=edge.wait(child_id,timeout=1800)
        if job['state']!='finished':raise ValueError('remaining_amend_unknown_no_replay:'+child_id)
        reconcile_finished_amend(authority,child,job)
        combined=applied+job['result']['rows']
        expected={(r['offer_id'],r['item'],r['sku']):Decimal(str(r['new_deduct'])) for r in body['rows']}
        observed={(r['offer_id'],r['item'],s):Decimal(str(v)) for r in combined for s,v in r['values'].items()}
        if observed!=expected or len(observed)!=sum(len(r['values']) for r in combined):raise ValueError('combined_amend_not_complete')
        terminal={'state':'verified_saved','claim_id':cid,'job_id':source_id,'rows':combined,
                  'automatic_retry':False,'reconciled_from_read':read_id,'remaining_child_job':child_id}
        path=persist(folder/'verified-combined.json',terminal)
        envelope=dict(terminal,evidence_path=path,recording=old.get('recording'))
        db.execute('CREATE TABLE IF NOT EXISTS campaign_partial_amend_reconciliation(id TEXT PRIMARY KEY,previous_result TEXT,new_result TEXT)')
        db.execute('BEGIN IMMEDIATE')
        try:
            current=db.execute('SELECT state,result FROM campaign_transfer_jobs WHERE id=?',(source_id,)).fetchone()
            if current['state']!='unknown' or current['result']!=source['result']:raise ValueError('original_amend_changed')
            db.execute('INSERT INTO campaign_partial_amend_reconciliation VALUES(?,?,?)',(source_id,source['result'],json.dumps(envelope,ensure_ascii=False)))
            db.execute("UPDATE campaign_transfer_jobs SET state='finished',result=? WHERE id=?",(json.dumps(envelope,ensure_ascii=False),source_id))
            db.execute('COMMIT')
        except BaseException:db.execute('ROLLBACK');raise
        return {'source_id':source_id,'state':'reconciled','already_saved_products':len(applied),'remaining_child_job':child_id}
    finally:authority.close();db.close()


def load_json(value):
    return json.loads(value or '{}')

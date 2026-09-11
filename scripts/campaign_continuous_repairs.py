"""Frozen-rule repairs of exact failed scope; no rotation or ERP price writes."""
from decimal import Decimal
import json
from pathlib import Path
import re
import uuid

from campaign_continuous_policy import RULE_SHA,classify_items,fingerprint,load_rules
from campaign_entry_authority import file_sha,load


def table(authority):
    authority.db.execute('''CREATE TABLE IF NOT EXISTS continuous_discount_repairs(
        id TEXT PRIMARY KEY, body TEXT NOT NULL, state TEXT NOT NULL, job_id TEXT, receipt TEXT)''')


def verify_claim(authority,cid,*,consume_job=None):
    load_rules()
    if not re.fullmatch('[0-9a-f]{32}',str(cid)):raise ValueError('invalid_repair_claim')
    table(authority)
    if consume_job is not None:authority.db.execute('BEGIN IMMEDIATE')
    try:
        row=authority.db.execute('SELECT * FROM continuous_discount_repairs WHERE id=?',(cid,)).fetchone()
        if row is None or row['state']!='claimed_not_dispatched':raise ValueError('repair_not_fresh_do_not_replay')
        body=json.loads(row['body'])
        if body['rule_sha']!=RULE_SHA:raise ValueError('repair_rule_changed')
        for ref in body['sources']:
            if file_sha(ref['path'])!=ref['sha256']:raise ValueError('repair_source_changed')
        blocked=authority.blocked(body['campaign'],'signup',body['start'],body['end'])
        if any(r['item'] in blocked for r in body['rows']):raise ValueError('repair_success_or_unknown_scope_protected')
        report=load(body['report_path']);repairs,_=classify_items(report['errors'])
        offers=authority.discount_offers()
        for r in body['rows']:
            errors=[e for e in report['errors'] if (e['item'],e['sku'])==(r['item'],r['sku'])]
            decisions=[d for d in repairs.get(r['item'],[]) if d['sku']==r['sku']]
            if len(errors)!=1 or len(decisions)!=1 or decisions[0]['repair']['kind']!='ordinary_discount':
                raise ValueError('repair_not_proven_ordinary_failed_sku')
            e=errors[0]
            if (Decimal(e['current_deduct'])!=Decimal(r['old_deduct'])
                    or Decimal(e['proposed_deduct'])!=Decimal(r['new_deduct'])
                    or abs(Decimal(e['feasible_final_price'])-Decimal(e['erp_final_target']))>Decimal('2')):
                raise ValueError('repair_amount_outside_frozen_rule')
            candidates=[o for o in offers if o.get('platform_offer_id',o['offer_id'])==r['offer_id']
                        and (o['start'],o['end'])==(body['start'],body['end'])
                        and any(i['item']==r['item'] and i['status']=='success' for i in o['items'])]
            actual=[a for o in candidates for a in o['rows'] if (a['item'],a['sku'])==(r['item'],r['sku'])]
            if len(candidates)!=1 or len(actual)!=1 or Decimal(actual[0]['deduct'])!=Decimal(r['old_deduct']):
                raise ValueError('repair_saved_old_amount_changed')
        if consume_job is not None:
            if not re.fullmatch('[0-9a-f]{64}',str(consume_job)):raise ValueError('invalid_repair_job')
            authority.db.execute("UPDATE continuous_discount_repairs SET state='dispatched_unknown',job_id=? WHERE id=?",(consume_job,cid))
            authority.db.execute('COMMIT')
        return dict(body,claim_id=cid,verified_claim=True,dispatch_consumed=consume_job is not None,job_id=consume_job)
    except BaseException:
        if consume_job is not None and authority.db.in_transaction:authority.db.execute('ROLLBACK')
        raise


def apply_verified_amendments(authority,offers):
    exists=authority.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='continuous_discount_repairs'").fetchone()
    if not exists:return offers
    for record in authority.db.execute("SELECT * FROM continuous_discount_repairs WHERE state='verified' ORDER BY rowid"):
        body=json.loads(record['body']);ref=json.loads(record['receipt'])
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('continuous_repair_receipt_changed')
        proof=load(ref['path'])
        if proof.get('claim_id')!=record['id'] or proof.get('job_id')!=record['job_id']:
            raise ValueError('continuous_repair_receipt_identity_changed')
        actual={(r['offer_id'],r['item'],s):v for r in proof['rows'] for s,v in r['values'].items()}
        for change in body['rows']:
            key=change['offer_id'],change['item'],change['sku']
            if key not in actual or Decimal(actual[key])!=Decimal(change['new_deduct']):
                raise ValueError('continuous_repair_saved_amount_not_proven')
            candidates=[o for o in offers if o.get('platform_offer_id',o['offer_id'])==change['offer_id']
                        and (o['start'],o['end'])==(body['start'],body['end'])]
            rows=[r for o in candidates for r in o['rows'] if (r['item'],r['sku'])==key[1:]]
            if len(rows)!=1 or Decimal(rows[0]['deduct']) not in (Decimal(change['old_deduct']),Decimal(change['new_deduct'])):
                raise ValueError('continuous_repair_overlay_conflict')
            rows[0].update(deduct=change['new_deduct'],amendment_receipt=ref)
    return offers


def execute_repairs(transport,action_id,payload,folder):
    from campaign_continuous_transport import persist
    a=transport.authority;table(a)
    report_path=transport.root/'reports'/(str(payload['failed_batch'])+'.json')
    report=load(report_path);repairs,exceptions=classify_items(report['errors'])
    if any(repairs.get(i)!=payload['decisions'][i] for i in payload['items']):
        raise ValueError('repair_decisions_do_not_match_official_report')
    body=a.get_bundle(report['bundle_id']);ordinary=[];custom=[];local_items=set()
    actual=load(transport.root/'verified-discounts'/(report['bundle_id']+'.json')) if any(
        d['repair']['kind']=='ordinary_discount' for ds in payload['decisions'].values() for d in ds) else {'rows':[]}
    for item,decisions in payload['decisions'].items():
        for d in decisions:
            repair=d['repair'];pair=item,d['sku']
            if repair['kind']=='ordinary_discount':
                errors=[e for e in report['errors'] if (e['item'],e['sku'])==pair]
                rows=[r for r in actual['rows'] if (r['item'],r['sku'])==pair]
                if len(errors)!=1 or len(rows)!=1:raise ValueError('exact_repair_discount_evidence_missing')
                e=errors[0];ordinary.append(dict(item=item,sku=d['sku'],offer_id=rows[0]['offer_id'],
                    old_deduct=e['current_deduct'],new_deduct=e['proposed_deduct']))
            elif repair['kind']=='custom_price':
                custom.append(dict(item=item,sku=d['sku'],activity_price=repair['price']))
                local_items.add(item)
            elif repair['kind']=='file_price':local_items.add(item) # Generator already writes ERP daily.
            else:raise ValueError('unsupported_repair_kind_no_browser_fallback')
    if custom:
        auth={'campaign':body['campaign'],'continuous_rule_sha':RULE_SHA,'authorized_custom_prices':custom,
              'source_report':str(report_path),'source_report_sha256':file_sha(report_path)}
        failure={'campaign':body['campaign'],'terminal':True,'batch_id':payload['failed_batch'],
                 'rows':[dict(item=r['item'],sku=r['sku'],status='failed') for r in custom],
                 'source_terminal':report['source_terminal']}
        ap=persist(folder/'custom-authorization.json',auth);fp=persist(folder/'failed-custom-rows.json',failure)
        entries=[dict(r,authorization_path=ap,authorization_sha256=file_sha(ap),
                      failure_path=fp,failure_sha256=file_sha(fp)) for r in custom]
        persist(transport.root/'repairs'/('custom-'+action_id+'.json'),{'rows':entries})
    if ordinary:
        ref=folder/'repair-claim.json'
        if ref.exists():cid=load(ref)['claim_id']
        else:
            cid=uuid.uuid4().hex
            claim=dict(rule_sha=RULE_SHA,campaign=body['campaign'],start=body['start'],end=body['end'],
                       rows=ordinary,report_path=str(report_path),sources=[{'path':str(p),'sha256':file_sha(p)}
                           for p in (report_path,Path(body['snapshot_path']),Path(report['source_terminal']))])
            a.db.execute('INSERT INTO continuous_discount_repairs VALUES(?,?,?,NULL,NULL)',
                         (cid,json.dumps(claim,ensure_ascii=False),'claimed_not_dispatched'))
            persist(ref,{'claim_id':cid})
        verify_claim(a,cid)
        job=transport.job('discount_amend',{'identity':transport.identity(payload),'claim_id':cid},folder)
        result=job.get('result') or {};binding=a.db.execute('SELECT * FROM continuous_discount_repairs WHERE id=?',(cid,)).fetchone()
        if binding['state']!='dispatched_unknown' or binding['job_id']!=job['job_id']:
            raise ValueError('repair_job_binding_mismatch')
        if result.get('state')!='verified_saved' or result.get('claim_id')!=cid:
            raise ValueError('repair_actual_readback_missing')
        proof_path=Path(result['evidence_path']);proof=load(proof_path)
        if any(result.get(k)!=proof.get(k) for k in ('state','claim_id','rows','job_id')):
            raise ValueError('repair_readback_observation_changed')
        observed={(r['offer_id'],r['item'],s):v for r in proof['rows'] for s,v in r['values'].items()}
        expected={(r['offer_id'],r['item'],r['sku']):r['new_deduct'] for r in ordinary}
        if set(observed)!=set(expected) or any(Decimal(observed[k])!=Decimal(v) for k,v in expected.items()):
            raise ValueError('repair_readback_full_scope_mismatch')
        if any(r['window']['start']!=body['start'] or r['window']['end']!=body['end'] for r in proof['rows']):
            raise ValueError('repair_readback_time_mismatch')
        a.db.execute("UPDATE continuous_discount_repairs SET state='verified',receipt=? WHERE id=?",
                     (json.dumps({'path':str(proof_path),'sha256':file_sha(proof_path)}),cid))
        local_items.update(r['item'] for r in ordinary)
    return dict(batch=payload['failed_batch'],items=[dict(item=i,outcome='success' if i in local_items else 'failed',
               changed=i in local_items) for i in payload['items']],rotation_performed=False)


if __name__=='__main__':
    import argparse
    from campaign_entry_authority import Authority
    p=argparse.ArgumentParser();p.add_argument('--claim',required=True);p.add_argument('--consume-job')
    args=p.parse_args();a=Authority()
    try:print(json.dumps(verify_claim(a,args.claim,consume_job=args.consume_job),ensure_ascii=False))
    finally:a.close()

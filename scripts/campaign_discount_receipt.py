"""Bind a created offer's official terminal and full SKU readback to its claim."""
from decimal import Decimal
import json
from pathlib import Path

from campaign_entry_authority import file_sha,load


def reconcile_discount(authority,job,*,output_dir):
    if job.get('operation')!='discount' or job.get('state')!='finished':
        raise ValueError('discount_job_has_no_verified_terminal')
    result=job.get('result') or {};claim=result.get('claim') or {};claim_id=claim.get('claim_id')
    binding=authority.db.execute('SELECT transport,state,job_id FROM claim_transports WHERE claim_id=?',(claim_id,)).fetchone()
    if not binding or tuple(binding)!=('dedicated_edge_v1','dispatched_unknown',job.get('job_id')):
        raise ValueError('discount_job_not_bound_to_claim')
    attempts=list(authority.db.execute('SELECT * FROM attempts WHERE id LIKE ?',(claim_id+':%',)))
    if not attempts or any(r['phase']!='discount' for r in attempts):raise ValueError('discount_claim_missing')
    first=attempts[0];body=authority.get_bundle(first['bundle_id'])
    rows=body['discount_rows'];expected={(r['item'],r['sku']):Decimal(r['deduct']) for r in rows}
    files=[f for f in body['files'] if Path(f['path']).name=='单品立减.xlsx']
    items=sorted({r['item'] for r in rows})
    if (len(files)!=1 or claim.get('file')!=files[0] or sorted(claim.get('items',[]))!=items
            or any(claim.get(k)!=first[k] for k in ('campaign','phase','start','end'))):
        raise ValueError('discount_claim_file_or_scope_changed')
    path=Path(result['evidence_path']).resolve(strict=True);saved=load(path)
    if any(saved.get(k)!=v for k,v in result.items() if k not in ('evidence_path','recording')):
        raise ValueError('discount_observation_changed')
    partial=result.get('state')=='verified_partial_offer_terminal'
    if (result.get('state') not in ('verified_offer_terminal','verified_partial_offer_terminal')
            or type(result.get('failed')) is not int or result['failed']<0
            or partial!=(result['failed']>0)
            or type(result.get('success')) is not int or result['success']<=0
            or result['success']+result['failed']!=len(expected)
            or not str(result.get('offer_id','')).isdigit() or result.get('offer_window_readback_required') is not False
            or any(result.get(k)!=first[k] for k in ('start','end'))):
        raise ValueError('discount_official_terminal_or_readback_incomplete')
    failures=[]
    if partial:
        from campaign_discount_failure_report import verify_failure_rows
        feedback=result['feedback'];report=Path(feedback['path']).resolve(strict=True)
        if not report.is_relative_to(path.parent) or report.suffix.lower()!='.xlsx':
            raise ValueError('discount_feedback_not_in_same_job')
        failures=verify_failure_rows(report,feedback['sha256'],set(expected),result['failed'])
        if failures!=result.get('failure_rows'):raise ValueError('discount_feedback_parsed_rows_changed')
    failed_pairs={(r['item'],r['sku']) for r in failures}
    expected_success={k:v for k,v in expected.items() if k not in failed_pairs}
    actual={}
    for row in result.get('readbacks',[]):
        window=row['window']
        if any(window.get(k)!=result[k] for k in ('offer_id','start','end')):
            raise ValueError('discount_saved_window_mismatch')
        for sku,value in row['values'].items():
            key=(row['item'],sku)
            if key in actual:raise ValueError('discount_readback_duplicate_sku')
            actual[key]=Decimal(value)
    if actual!=expected_success:raise ValueError('discount_saved_sku_amount_mismatch')
    outcomes=[];entries=[]
    for item in items:
        pairs={p for p in expected if p[0]==item}
        outcome=('failed' if pairs.issubset(failed_pairs) else 'success' if not pairs.intersection(failed_pairs) else 'partial')
        outcomes.append({'item':item,'outcome':outcome})
        # A mixed product remains unknown in the existing authority, which
        # protects its imported SKUs from any whole-product replay.
        if outcome!='partial':entries.append({'item':item,'status':outcome})
    errors=[dict(r,kind='unknown',terminal='failed',batch=result['offer_id'],
                 official_evidence=result.get('feedback')) for r in failures]
    doc=dict(schema='campaign_entry_terminal_v1',claim_id=claim_id,campaign=first['campaign'],phase='discount',
             start=first['start'],end=first['end'],file_sha256=files[0]['sha256'],batch_id=result['offer_id'],
             terminal=True,items=entries,official_observation={'path':str(path),'sha256':file_sha(path)},
             readbacks=result['readbacks'],job_id=job['job_id'])
    if partial:doc.update(feedback=result['feedback'],failure_rows=failures,errors=errors,
                          partial_items=[r['item'] for r in outcomes if r['outcome']=='partial'])
    target=Path(output_dir)/(claim_id+'-discount-terminal.json');target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if load(target)!=doc:raise ValueError('immutable_discount_receipt_conflict')
    else:
        with target.open('x',encoding='utf-8') as stream:json.dump(doc,stream,ensure_ascii=False,indent=2)
    authority.terminal(claim_id,dict(doc,evidence_path=str(target.resolve())))
    if partial:
        authority.db.execute('CREATE TABLE IF NOT EXISTS verified_partial_discount_receipts(bundle_id TEXT PRIMARY KEY,path TEXT NOT NULL,sha256 TEXT NOT NULL)')
        existing=authority.db.execute('SELECT path,sha256 FROM verified_partial_discount_receipts WHERE bundle_id=?',(first['bundle_id'],)).fetchone()
        proof=(str(target.resolve()),file_sha(target))
        if existing is not None and tuple(existing)!=proof:raise ValueError('partial_discount_evidence_is_immutable')
        authority.db.execute('INSERT OR IGNORE INTO verified_partial_discount_receipts VALUES(?,?,?)',(first['bundle_id'],*proof))
    return dict(status='terminal',batch=result['offer_id'],items=outcomes,
                evidence=str(target.resolve()),errors=errors,platform_write=False,
                partial_failure_report_verified=partial)

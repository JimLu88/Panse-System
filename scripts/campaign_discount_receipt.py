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
    if (result.get('state')!='verified_offer_terminal' or result.get('failed')!=0
            or type(result.get('success')) is not int or result['success']!=len(expected)
            or not str(result.get('offer_id','')).isdigit() or result.get('offer_window_readback_required') is not False
            or any(result.get(k)!=first[k] for k in ('start','end'))):
        raise ValueError('discount_official_terminal_or_readback_incomplete')
    actual={}
    for row in result.get('readbacks',[]):
        window=row['window']
        if any(window.get(k)!=result[k] for k in ('offer_id','start','end')):
            raise ValueError('discount_saved_window_mismatch')
        for sku,value in row['values'].items():
            key=(row['item'],sku)
            if key in actual:raise ValueError('discount_readback_duplicate_sku')
            actual[key]=Decimal(value)
    if actual!=expected:raise ValueError('discount_saved_sku_amount_mismatch')
    entries=[{'item':i,'status':'success'} for i in items]
    doc=dict(schema='campaign_entry_terminal_v1',claim_id=claim_id,campaign=first['campaign'],phase='discount',
             start=first['start'],end=first['end'],file_sha256=files[0]['sha256'],batch_id=result['offer_id'],
             terminal=True,items=entries,official_observation={'path':str(path),'sha256':file_sha(path)},
             readbacks=result['readbacks'],job_id=job['job_id'])
    target=Path(output_dir)/(claim_id+'-discount-terminal.json');target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if load(target)!=doc:raise ValueError('immutable_discount_receipt_conflict')
    else:
        with target.open('x',encoding='utf-8') as stream:json.dump(doc,stream,ensure_ascii=False,indent=2)
    authority.terminal(claim_id,dict(doc,evidence_path=str(target.resolve())))
    return dict(status='terminal',batch=result['offer_id'],items=[{'item':i,'outcome':'success'} for i in items],
                evidence=str(target.resolve()),errors=[],platform_write=False)

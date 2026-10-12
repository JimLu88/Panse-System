"""A complete official rejection is a failed terminal, not an unknown success."""
import json
import re
from pathlib import Path
from campaign_entry_authority import load,file_sha


def failed_identity(result,expected_count):
    ids=set(re.findall(r'活动创建成功[，,]\s*活动ID为[：:]\s*(\d+)',result.get('text','')))
    if (result.get('state')!='official_import_terminal'
            or type(result.get('success')) is not int or result['success']!=0
            or type(result.get('failed')) is not int or result['failed']!=expected_count or expected_count<=0
            or result.get('offer_readback_gate')!='no_successful_offer_to_read'
            or result.get('readbacks') or len(ids)!=1):
        raise ValueError('complete_discount_rejection_not_proven')
    return next(iter(ids))


def reconcile(authority,job,body,first,files,expected,path,output_dir):
    from campaign_discount_failure_report import verify_failure_rows
    r=job['result'];claim_id=r['claim']['claim_id'];batch=failed_identity(r,len(expected))
    if any(r.get(k)!=first[k] for k in ('start','end')):
        raise ValueError('discount_failed_window_mismatch')
    feedback=r['feedback'];report=Path(feedback['path']).resolve(strict=True)
    if report.suffix.lower()!='.xlsx' or not report.is_relative_to(path.parent):
        raise ValueError('discount_feedback_not_in_same_job')
    failures=verify_failure_rows(report,feedback['sha256'],set(expected),r['failed'])
    if failures!=r.get('failure_rows') or {(x['item'],x['sku']) for x in failures}!=set(expected):
        raise ValueError('discount_failed_full_scope_not_proven')
    items=sorted({p[0] for p in expected})
    errors=[dict(x,kind='unknown',terminal='failed',batch=batch,official_evidence=feedback) for x in failures]
    doc=dict(schema='campaign_entry_terminal_v1',claim_id=claim_id,campaign=first['campaign'],phase='discount',
        start=first['start'],end=first['end'],file_sha256=files[0]['sha256'],batch_id=batch,terminal=True,
        items=[dict(item=i,status='failed') for i in items],official_observation=dict(path=str(path),sha256=file_sha(path)),
        readbacks=[],job_id=job['job_id'],feedback=feedback,failure_rows=failures,errors=errors,
        all_import_rows_failed=True,successful_offer_verified=False)
    target=Path(output_dir)/(claim_id+'-discount-terminal.json');target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if load(target)!=doc:raise ValueError('immutable_discount_receipt_conflict')
    else:
        with target.open('x',encoding='utf-8') as f:json.dump(doc,f,ensure_ascii=False,indent=2)
    authority.terminal(claim_id,dict(doc,evidence_path=str(target.resolve())))
    return dict(status='terminal',batch=batch,items=[dict(item=i,outcome='failed') for i in items],
                evidence=str(target.resolve()),errors=errors,platform_write=False,partial_failure_report_verified=False)

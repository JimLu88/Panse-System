"""Reconcile a bound Edge upload job into the existing per-item authority.

Read-only toward Taobao. A job finishing, upload click or video is NOT success.
The exact new batch and complete official counts/report supply the outcome.
"""
import json
from pathlib import Path

from campaign_entry_authority import file_sha, load
from campaign_official_failure_report import parse_feedback


def reconcile_signup(authority, job, *, output_dir):
    if job.get('operation') != 'signup' or job.get('state') != 'finished':
        raise ValueError('edge_signup_job_has_no_official_terminal')
    result = job.get('result') or {}
    claim = result.get('claim') or {}
    claim_id = claim.get('claim_id')
    binding = authority.db.execute(
        'SELECT transport,state,job_id FROM claim_transports WHERE claim_id=?', (claim_id,)).fetchone()
    if not binding or tuple(binding) != ('dedicated_edge_v1','dispatched_unknown',job.get('job_id')):
        raise ValueError('edge_job_not_bound_to_dispatched_claim')
    attempts = list(authority.db.execute('SELECT * FROM attempts WHERE id LIKE ?', (claim_id+':%',)))
    if not attempts or any(r['phase'] != 'signup' for r in attempts):
        raise ValueError('signup_claim_missing')
    first = attempts[0]
    body = authority.get_bundle(first['bundle_id'])
    files = [f for f in body['files'] if Path(f['path']).name == '活动报名.xlsx']
    items = sorted(r['item'] for r in attempts)
    if (len(files) != 1 or claim.get('file') != files[0] or sorted(claim.get('items') or []) != items
            or any(claim.get(k) != first[k] for k in ('campaign','phase','start','end'))):
        raise ValueError('edge_claim_identity_or_file_changed')
    evidence_path = Path(result['evidence_path']).resolve(strict=True)
    evidence = load(evidence_path)
    # Job payload must agree with the observation persisted before parsing.
    if any(result.get(k) != evidence.get(k) for k in ('state','batch','record','counts')):
        raise ValueError('edge_batch_observation_changed')
    batch = str(evidence.get('batch') or '')
    record = evidence.get('record') or {}
    if (not batch.isdigit() or record.get('历史记录ID') != batch
            or record.get('执行操作') != '商品批量导入'
            or record.get('数据来源') != Path(files[0]['path']).name):
        raise ValueError('official_batch_file_identity_invalid')
    if evidence.get('state') == 'terminal_processing_failed':
        if record.get('执行状态') not in ('失败','处理失败'):
            raise ValueError('official_processing_failure_not_proven')
        outcomes = [{'item':i,'outcome':'failed'} for i in items]
        errors = [dict(item=i,sku='',kind='unknown',terminal='failed',batch=batch,
                       message=record.get('执行结果') or '平台文件处理失败',
                       official_evidence={'path':str(evidence_path),'sha256':file_sha(evidence_path)}) for i in items]
    elif evidence.get('state') == 'terminal':
        counts = evidence.get('counts') or {}
        if (set(counts) != {'total','success','failed','pending'}
                or any(type(v) is not int or v < 0 for v in counts.values())
                or counts['total'] != len(items) or counts['pending'] != 0
                or counts['total'] != counts['success']+counts['failed']
                or record.get('执行状态') != '成功'):
            raise ValueError('official_batch_counts_incomplete')
        feedback = result.get('feedback')
        if feedback:
            if str(feedback.get('batch')) != batch:
                raise ValueError('feedback_batch_changed')
            parsed = parse_feedback(Path(feedback['path']).read_bytes(), expected_sha=feedback['sha256'],
                                    batch=batch, expected_items=items, official_counts=counts)
            outcomes, errors = parsed['outcomes'], parsed['errors']
        elif counts['success'] == len(items) or counts['failed'] == len(items):
            outcome = 'success' if counts['success'] == len(items) else 'failed'
            outcomes = [{'item':i,'outcome':outcome} for i in items]
            errors = []  # Missing failure report stays a report gate, not invented reasons.
        else:
            raise ValueError('mixed_batch_requires_exact_official_failure_report')
    else:
        raise ValueError('official_batch_still_unknown_do_not_replay')
    rows = sorted(({'item':r['item'],'status':r['outcome']} for r in outcomes), key=lambda r:r['item'])
    doc = dict(schema='campaign_entry_terminal_v1',claim_id=claim_id,campaign=first['campaign'],
               phase='signup',start=first['start'],end=first['end'],file_sha256=files[0]['sha256'],
               batch_id=batch,terminal=True,items=rows,job_id=job['job_id'],
               official_observation={'path':str(evidence_path),'sha256':file_sha(evidence_path)},
               feedback=result.get('feedback'),errors=errors)
    output = Path(output_dir); output.mkdir(parents=True,exist_ok=True)
    path = output/(claim_id+'-official-terminal.json')
    if path.exists():
        if load(path) != doc:
            raise ValueError('immutable_official_terminal_conflict')
    else:
        with path.open('x',encoding='utf-8') as stream:
            json.dump(doc,stream,ensure_ascii=False,indent=2)
    authority.terminal(claim_id,dict(doc,evidence_path=str(path.resolve())))
    return {'status':'terminal','batch':batch,'items':outcomes,'errors':errors,
            'evidence':str(path.resolve()),'report_gate':result.get('report_gate'),
            'platform_write':False}

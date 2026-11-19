"""Read-only proof for resuming the original completed scope export."""
import json
from pathlib import Path
import re
import sqlite3
from campaign_continuous_policy import fingerprint
from campaign_entry_authority import load,file_sha


def verify(root,request_id,*,transfer_db=None,artifact_roots=None):
    root=Path(root).resolve()
    if not re.fullmatch('[0-9a-f]{64}',request_id) or root.name!=request_id:
        raise ValueError('exact_scope_request_required')
    request=load(root/'request.json')
    registered=root.parent.parent/'requests'/(request_id+'.json')
    if fingerprint(request)!=request_id or load(registered)!=request:
        raise ValueError('original_scope_request_changed')
    from campaign_continuous_execute import validate_request
    validate_request(request)
    if request.get('existing_product_export'):
        raise ValueError('original_owned_export_required')
    if (root/'segments').exists() or (root/'time-request.json').exists():
        raise ValueError('scope_already_progressed')
    with sqlite3.connect((root.parent.parent/'jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        record=db.execute('SELECT state,result FROM continuous_jobs WHERE id=?',(request_id,)).fetchone()
        if (not record or record[0]!='blocked' or
                (json.loads(record[1]) or {}).get('blocker',{}).get('step')!='scope'
                or db.execute("SELECT 1 FROM continuous_jobs WHERE state='running'").fetchone()):
            raise ValueError('idle_blocked_scope_required')
    with sqlite3.connect((root/'controller.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT a.id,a.step,a.status,r.owner,a.payload_sha FROM continuous_campaign_actions a '
                        'JOIN continuous_campaign_runs r ON r.id=a.run_id').fetchall()
    if len(rows)!=1 or rows[0][1:4]!=('scope','interrupted_read',None):
        raise ValueError('one_original_interrupted_scope_required')
    action=rows[0][0];folder=root/'shared/actions'/action
    saved=load(folder/'request.json');payload=saved['payload']
    if (saved.get('step')!='scope' or fingerprint(payload)!=rows[0][4]
            or payload.get('identity') not in request['pages'].values()):
        raise ValueError('original_scope_payload_changed')
    ref=load(folder/'product_export-job.json')
    job_id=fingerprint(['product_export',payload['identity']['shop_id'],action])
    if ref!={'step':'product_export','job_id':job_id}:
        raise ValueError('original_scope_export_identity_changed')
    agent=Path('D:/AI/畔色ERP系统/Web-Agent程序')
    import sys
    if str(agent) not in sys.path:sys.path.insert(0,str(agent))
    from app.engine.campaign_transfer_jobs import TransferJobs
    job=TransferJobs.persisted_status(transfer_db or agent/'data/output/campaign-transfers/jobs.sqlite',job_id)
    if job.get('state')!='finished' or job.get('operation')!='product_export' or job.get('job_id')!=job_id:
        raise ValueError('original_export_not_finished')
    from campaign_product_scope import from_edge_job
    scope=from_edge_job(job,expected_request_id=action,expected_shop=payload['identity']['shop_id'],
        roots=artifact_roots or [agent/'data/output',root.parent.parent.parent])
    references=[root/'request.json',registered,root/'shared/snapshot.json',folder/'request.json',
                folder/'product_export-job.json',folder/'product_export-observation.json',root/'process.log']
    return dict(schema='campaign_scope_checkpoint_v1',request_id=request_id,action_id=action,
        export_job_id=job_id,export_result_sha256=fingerprint(job.get('result')),
        observed_item_count=scope['observed_item_count'],sku_count=len(scope['sku_facts']),
        references=[dict(path=str(p),sha256=file_sha(p)) for p in references],
        platform_write=False,request_changed=False,claims_released=False)


def check_references(proof):
    if proof.get('schema')!='campaign_scope_checkpoint_v1':raise ValueError('scope_checkpoint_schema_changed')
    for ref in proof['references']:
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('scope_checkpoint_evidence_changed')

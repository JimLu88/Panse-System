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
    if proof.get('schema') not in ('campaign_scope_checkpoint_v1','campaign_discount_checkpoint_v1'):
        raise ValueError('scope_checkpoint_schema_changed')
    for ref in proof['references']:
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('scope_checkpoint_evidence_changed')


def verify_discount(root,request_id,*,transfer_db=None,agent_root=None,authority_db=None):
    """Maintenance only after the original partial discount was fully read back."""
    root=Path(root).resolve()
    if not re.fullmatch('[0-9a-f]{64}',request_id) or root.name!=request_id:
        raise ValueError('exact_discount_request_required')
    request=load(root/'request.json');registered=root.parent.parent/'requests'/(request_id+'.json')
    if fingerprint(request)!=request_id or load(registered)!=request:raise ValueError('original_request_changed')
    from campaign_continuous_execute import validate_request
    validate_request(request)
    with sqlite3.connect((root.parent.parent/'jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        controller=db.execute('SELECT state,result FROM continuous_jobs WHERE id=?',(request_id,)).fetchone()
        if not controller or controller[0] not in ('blocked','unknown') or db.execute("SELECT 1 FROM continuous_jobs WHERE state='running'").fetchone():
            raise ValueError('idle_blocked_discount_required')
    with sqlite3.connect((root/'controller.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute("SELECT id,step,status,payload_sha FROM continuous_campaign_actions WHERE status<>'done'").fetchall()
        if db.execute('SELECT 1 FROM continuous_campaign_runs WHERE owner IS NOT NULL').fetchone():
            raise ValueError('discount_controller_owner_active')
    if len(rows)!=1 or rows[0][1:3]!=('discount','unknown'):
        raise ValueError('one_original_unreconciled_discount_required')
    action,_,_,sha=rows[0];folders=list((root/'segments').glob('*/actions/'+action))
    if len(folders)!=1:raise ValueError('original_discount_folder_not_unique')
    folder=folders[0];saved=load(folder/'request.json')
    if saved.get('step')!='discount' or fingerprint(saved['payload'])!=sha:
        raise ValueError('original_discount_payload_changed')
    ref=load(folder/'discount-job.json');claim=load(folder/'claim.json')['claim_id']
    jid=fingerprint(['discount',claim])
    if ref!={'step':'discount','job_id':jid}:raise ValueError('original_discount_job_changed')
    agent=Path(agent_root or 'D:/AI/畔色ERP系统/Web-Agent程序');database=Path(transfer_db or agent/'data/output/campaign-transfers/jobs.sqlite')
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT state,operation,result FROM campaign_transfer_jobs WHERE id=?',(jid,)).fetchone()
        audit=db.execute('SELECT read_job_id,previous_result,new_result FROM campaign_discount_terminal_recovery WHERE id=?',(jid,)).fetchone()
        if not row or row[:2]!=('finished','discount') or not audit:
            raise ValueError('original_discount_verified_readback_required')
        result=json.loads(row[2]);prior=json.loads(audit[1])
        if result!=json.loads(audit[2]) or result.get('state')!='verified_partial_offer_terminal':
            raise ValueError('audited_discount_readback_changed')
        if (result.get('claim')!=prior.get('claim') or result.get('claim',{}).get('claim_id')!=claim
                or any(result.get(k)!=prior.get(k) for k in ('success','failed','start','end'))):
            raise ValueError('discount_business_identity_changed')
        read=db.execute('SELECT state,operation,result FROM campaign_transfer_jobs WHERE id=?',(audit[0],)).fetchone()
        if not read or read[:2]!=('finished','discount_terminal_recover'):
            raise ValueError('finished_discount_read_job_required')
        reading=json.loads(read[2]);terminal=reading.get('terminal') or {}
        if reading.get('source_job_id')!=jid or dict(terminal,recording=reading.get('recording'))!=result:
            raise ValueError('discount_read_job_terminal_changed')
    import sys
    if str(agent) not in sys.path:sys.path.insert(0,str(agent))
    from app.engine.campaign_discount_terminal_recovery import check_rows
    check_rows(result['readbacks'],dict(result['claim'],offer_id=result['offer_id'],success=result['success']),result['failure_rows'])
    feedback=result['feedback']
    if file_sha(feedback['path'])!=feedback['sha256']:raise ValueError('discount_report_bytes_changed')
    terminal_path=Path(terminal['evidence_path']).resolve(strict=True)
    if not terminal_path.is_relative_to((agent/'data/output/campaign-transfers'/audit[0]).resolve()):
        raise ValueError('discount_terminal_outside_original_read_job')
    if load(terminal_path)!={k:v for k,v in terminal.items() if k!='evidence_path'}:
        raise ValueError('discount_terminal_file_changed')
    refs=[root/'request.json',registered,root/'shared/snapshot.json',folder/'request.json',
          folder/'discount-job.json',folder/'claim.json',terminal_path,Path(feedback['path'])]
    collision=None
    if controller[0]=='unknown':
        collision,extra=discount_observation_collision(root,request_id,folder,jid,result,controller,
                                                     authority_db=authority_db)
        refs+=extra
    return dict(schema='campaign_discount_checkpoint_v1',request_id=request_id,action_id=action,
                export_job_id=None,discount_job_id=jid,read_job_id=audit[0],
                success=result['success'],failed=result['failed'],transfer_result_sha256=fingerprint(result),
                references=[dict(path=str(p),sha256=file_sha(p)) for p in refs],
                platform_write=False,request_changed=False,claims_released=False,local_collision=collision)


def discount_observation_collision(root,rid,folder,jid,result,controller,*,authority_db=None):
    """Prove a local immutable-observation collision happened before reconciliation."""
    from campaign_continuous_recovery import verified_discount_observation_upgrade
    expected={'reason':'controller_did_not_produce_terminal_receipt','automatic_retry':False}
    if controller[0]!='unknown' or json.loads(controller[1])!=expected:
        raise ValueError('exact_discount_observation_collision_required')
    audit=root/('discount-terminal-recovery-'+jid+'.json')
    log=root/('process-discount-terminal-recovery-'+jid+'.log')
    text=log.read_text(encoding='utf-8');receipt=load(audit)
    if (not text.rstrip().endswith('ValueError: continuous_evidence_is_immutable')
            or 'in _recover_finished_phase' not in text or 'in persist' not in text
            or "reconciled-" not in text or 'discount_reconciliation' in text
            or receipt.get('request_id')!=rid or receipt.get('job_id')!=jid
            or receipt.get('write_claims_released') is not False
            or receipt.get('previous_controller',{}).get('state')!='blocked'
            or receipt.get('old_result_sha256')!=file_sha(root/'result.json')
            or (folder/'reconciled-result.json').exists()
            or (folder/(result['claim']['claim_id']+'-discount-terminal.json')).exists()
            or (root/'discount-observation-recovery.json').exists()):
        raise ValueError('discount_reconciliation_may_have_progressed')
    previous=folder/'reconciled-discount-observation.json'
    old=load(previous)
    current=dict(old,result=result)
    if not verified_discount_observation_upgrade(old,current):
        raise ValueError('discount_observation_upgrade_not_bound')
    from campaign_entry_authority import STATE
    with sqlite3.connect(Path(authority_db or STATE).resolve().as_uri()+'?mode=ro',uri=True) as db:
        claim=result['claim']['claim_id']
        binding=db.execute('SELECT transport,state,job_id FROM claim_transports WHERE claim_id=?',(claim,)).fetchone()
        attempts=db.execute('SELECT status FROM attempts WHERE id LIKE ?',(claim+':%',)).fetchall()
        if binding!=('dedicated_edge_v1','dispatched_unknown',jid) or not attempts or any(r!=('unknown',) for r in attempts):
            raise ValueError('discount_authority_already_changed')
    return dict(reason='verified_local_observation_collision_before_reconciliation',log_sha256=file_sha(log),
                previous_observation_sha256=file_sha(previous),authority_unchanged=True),[audit,log,previous,root/'result.json']

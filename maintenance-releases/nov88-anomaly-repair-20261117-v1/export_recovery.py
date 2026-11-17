"""One exact original, pre-page November 88 inventory continuation."""
import asyncio,json

JOB='625f3886b9672462f61eb28c6cd03a7df31450565e1066c4ecef049f12a77544'

def validate(row, root, fingerprint):
    if not row or row['id']!=JOB or row['operation']!='final_inventory' or row['state']!='interrupted_read':
        raise ValueError('exact_original_interrupted_inventory_required')
    result=json.loads(row['result']);request=json.loads(row['request'])
    frames=result.get('program_location',[])
    if result.get('error')!='AttributeError' or frames!=[
        {'file':'campaign_transfer_jobs.py','function':'_execute','line':944},
        {'file':'campaign_bound_transfers.py','function':'execute','line':442}]:
        raise ValueError('exact_pre_page_failure_required')
    rec=result.get('recording',{})
    if rec.get('state')!='unavailable_before_page_binding' or rec.get('video') or result.get('platform_write_started'):
        raise ValueError('pre_page_no_write_evidence_required')
    if request.get('request_sha')!=fingerprint(request.get('payload')) or row['request_sha']!=fingerprint({'operation':'final_inventory','request':request}):
        raise ValueError('original_request_hash_changed')
    identity=request['payload']['identity']
    if any(identity.get(k)!=v for k,v in dict(campaign_id='49727',phase_id='49734',sign_record_id='3594587083',start='2026-11-16 20:00:00',end='2026-11-19 23:59:59').items()):
        raise ValueError('original_campaign_changed')
    if root.exists() and (list(root.rglob('*.xlsx')) or list(root.rglob('export-request.json'))):
        raise ValueError('export_already_attempted_no_replay')
    return request

def resume(worker):
    from app.engine.campaign_transfer_jobs import fingerprint
    if worker.session.context is None:raise ValueError('dedicated_browser_not_connected')
    jobs=worker.jobs
    if jobs.busy() or any(not t.done() for t in jobs.tasks.values()):raise ValueError('worker_not_idle')
    row=jobs.db.execute('SELECT * FROM campaign_transfer_jobs WHERE id=?',(JOB,)).fetchone()
    request=validate(row,worker.output/JOB,fingerprint)
    jobs.db.execute('CREATE TABLE IF NOT EXISTS nov88_anomaly_export_recovery(id TEXT PRIMARY KEY,previous_result TEXT NOT NULL,previous_request TEXT NOT NULL)')
    jobs.db.execute('BEGIN IMMEDIATE')
    try:
        jobs.db.execute('INSERT INTO nov88_anomaly_export_recovery VALUES(?,?,?)',(JOB,row['result'],row['request']))
        changed=jobs.db.execute("UPDATE campaign_transfer_jobs SET state='running',result=NULL WHERE id=? AND state='interrupted_read' AND result=?",(JOB,row['result']))
        if changed.rowcount!=1:raise ValueError('original_job_changed')
        jobs.db.execute('COMMIT')
    except BaseException:
        jobs.db.execute('ROLLBACK');raise
    request['read_recovery_index']=1
    jobs.tasks[JOB]=asyncio.create_task(jobs._execute(JOB,'final_inventory',request))
    return dict(jobs.status(JOB),ok=True)

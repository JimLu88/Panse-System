"""Independent November 88 repair service; normal enrollment stays unchanged."""
from pathlib import Path
import importlib.util
import asyncio

ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-nov88-anomaly-repair-20261117/program')

def module(name):
    if name not in ('export_recovery','repair_core','repair_runtime','repair_resume','repair_remaining'):raise ValueError('fixed_module_required')
    spec=importlib.util.spec_from_file_location('nov88_anomaly_'+name,ROOT/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

async def handle(session,operation,options):
    if operation.startswith('program_nov88_anomaly_remaining_'):
        return await module('repair_remaining').handle(session,operation,options)
    if operation.startswith('program_nov88_anomaly_resume_') and operation!='program_nov88_anomaly_resume_export':
        return await module('repair_resume').handle(session,operation,options)
    if operation=='program_nov88_anomaly_resume_export':
        if options:raise ValueError('no_resume_overrides')
        from app.engine.campaign_enamel88_reason_read import idle
        idle(session)
        worker=getattr(session,'campaign_transfers',None)
        if worker is None:raise ValueError('original_worker_required')
        return module('export_recovery').resume(worker)
    allowed={'program_nov88_anomaly_capture':'capture','program_nov88_anomaly_capture_resume':'capture_resume','program_nov88_anomaly_prepare':'prepare','program_nov88_anomaly_inspect':'inspection','program_nov88_anomaly_apply':'apply','program_nov88_anomaly_status':'status','program_nov88_anomaly_verify':'verify'}
    if operation not in allowed or set(options)!={'request_sha256'}:raise ValueError('exact_anomaly_options_required')
    core=module('repair_core');request_sha=options['request_sha256'];core.validate_request(request_sha)
    stage=allowed[operation]
    if stage=='status':
        terminals={s:core.load(core.ROOT/s/'terminal.json') for s in ('capture','prepare','apply') if (core.ROOT/s/'terminal.json').exists()}
        return dict(ok=True,terminals=terminals,active=bool(getattr(session,'rotation_workflow_task',None) and not session.rotation_workflow_task.done()),platform_write=False)
    if stage=='verify':
        module('repair_runtime').checked_plan(request_sha)
        return dict(ok=True,ready=True,request_sha256=request_sha,platform_write=False)
    folder=core.ROOT/('capture-recovery-1' if stage=='capture_resume' else stage)
    if (folder/'terminal.json').exists():return dict(ok=True,terminal=core.load(folder/'terminal.json'),already_completed_no_replay=True)
    if (folder/'intent.json').exists():return dict(ok=True,state='already_started_no_replay',terminal=str(folder/'terminal.json'))
    from app.engine.campaign_enamel88_reason_read import idle
    idle(session)
    if session.context is None:raise ValueError('dedicated_browser_not_connected')
    runtime=module('repair_runtime')
    if stage=='apply':runtime.checked_plan(request_sha)
    action=getattr(runtime,stage)
    folder.mkdir(parents=True,exist_ok=True);core.save(folder/'intent.json',dict(request_sha256=request_sha,stage=stage,automatic_retry=False))
    async def run():
        try:await action(session,request_sha)
        except Exception as exc:
            if not (folder/'terminal.json').exists():core.save(folder/'terminal.json',dict(state='stopped_preserve_checkpoints',reason=str(exc)[:500],automatic_retry=False))
    session.rotation_workflow_job='nov88-anomaly-'+stage
    session.rotation_workflow_task=asyncio.create_task(run())
    return dict(ok=True,state='running',terminal=str(folder/'terminal.json'))

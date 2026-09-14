"""Program-owned completion of original checkpoints; never replay a write.

Only the four installed receipt reconcilers are eligible. Unknown jobs, login
gates and unsupported defects end with a durable exception, not AI polling.
"""
from pathlib import Path
import sqlite3

from campaign_entry_authority import load
from campaign_edge_client import EdgeJobError


def checkpoint(root):
    root=Path(root);dbpath=root/'controller.sqlite3'
    if not dbpath.exists():return None
    with sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT a.id,a.step,a.status,r.owner FROM continuous_campaign_actions a '
            'JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.status<>?',('done',)).fetchall()
    if len(rows)!=1 or rows[0][3] is not None:return None
    action,step,state,_=rows[0]
    allowed={'scope':('product_export',{'unknown','interrupted_read'}),
             'discount':('discount',{'unknown'}),'signup':('signup',{'unknown'}),
             'verify_discount_window':('discount_readback',{'interrupted_read'})}
    if step not in allowed or state not in allowed[step][1]:return None
    operation=allowed[step][0]
    folders=[root/'shared'/'actions'/action] if step=='scope' else list((root/'segments').glob('*/actions/'+action))
    if len(folders)!=1:return None
    ref=folders[0]/(operation+'-job.json')
    if not ref.exists():return None
    saved=load(ref)
    if saved.get('step')!=operation:return None
    return dict(action_id=action,step=step,operation=operation,job_id=saved['job_id'])


def reconcile(root,authority,edge,artifact_roots,point):
    from campaign_continuous_recovery import (recover_finished_scope,recover_finished_discount,
        recover_finished_signup,recover_finished_discount_window)
    methods={'scope':recover_finished_scope,'discount':recover_finished_discount,
             'signup':recover_finished_signup,'verify_discount_window':recover_finished_discount_window}
    kw={'artifact_roots':artifact_roots} if point['step'] in ('scope','verify_discount_window') else {}
    return methods[point['step']](root,authority,edge,**kw)


def execute_owned(request,*,root,authority,edge,artifact_roots,execute,progress=None):
    """One worker owns all steps. At most one reconciliation per old action.

    A known running original job gets one bounded read-only wait, never another
    submission. The original immutable evidence and every price guard survive.
    """
    from campaign_continuous_recovery import persist_run_outcome
    from campaign_continuous_transport import persist
    root=Path(root);recovered=set()
    for _ in range(64):
        try:
            result=execute(request,root=root,authority=authority,edge=edge,
                           artifact_roots=artifact_roots,progress=progress)
        except EdgeJobError as exc:
            result=dict(status='blocked',all_signed_up=False,segments=[],
                        blocker=dict(step='transport',reason=exc.reason,job_id=exc.job_id),legacy_fallback=False)
        if result.get('status')=='complete':return result
        point=checkpoint(root)
        if point is None or point['action_id'] in recovered:
            persist_run_outcome(root,result);return result
        try:
            job=edge.status(point['job_id'])
            if job.get('job_id')!=point['job_id'] or job.get('operation')!=point['operation']:
                raise ValueError('owned_original_job_identity_changed')
            if job.get('state')=='running':
                job=edge.wait(point['job_id'],timeout=300,progress=progress)
            if (job.get('state')!='finished' or job.get('job_id')!=point['job_id']
                    or job.get('operation')!=point['operation']):
                persist_run_outcome(root,result);return result
            receipt=reconcile(root,authority,edge,artifact_roots,point)
            persist(root/'owned-reconciliation'/(point['action_id']+'.json'),dict(
                checkpoint=point,receipt=receipt,platform_write=False,write_replayed=False))
            recovered.add(point['action_id'])
        except (ValueError,KeyError,OSError,EdgeJobError) as exc:
            # No free-browser fallback, process restart, claim reset or second
            # attempt. Preserve the reason without exception text/credentials.
            result=dict(result,owned_reconciliation=dict(state='needs_attention',
                action_id=point['action_id'],job_id=point['job_id'],error_type=type(exc).__name__,
                automatic_retry=False))
            persist_run_outcome(root,result);return result
    result=dict(result,owned_reconciliation=dict(state='needs_attention',reason='reconciliation_budget_reached'))
    persist_run_outcome(root,result);return result

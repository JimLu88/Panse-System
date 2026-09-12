"""Reconcile an existing finished discount job; no browser write or new claim."""
from pathlib import Path

from campaign_continuous_flow import Store
from campaign_continuous_policy import fingerprint
from campaign_continuous_transport import persist
from campaign_discount_receipt import reconcile_discount
from campaign_entry_authority import load


def recover_finished_discount(root,authority,edge):
    root=Path(root);store=Store(root/'controller.sqlite3')
    try:
        rows=store.db.execute('SELECT a.id,a.step,a.payload_sha,a.status,r.owner FROM continuous_campaign_actions a '
            'JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.status<>?',('done',)).fetchall()
        if len(rows)!=1 or rows[0][1]!='discount' or rows[0][3:]!=('unknown',None):
            raise ValueError('only_one_idle_unknown_discount_can_reconcile')
        action,_,sha,_,_=rows[0]
        folders=list((root/'segments').glob('*/actions/'+action))
        if len(folders)!=1:raise ValueError('discount_action_source_not_unique')
        folder=folders[0];saved=load(folder/'request.json');payload=saved['payload']
        if saved['step']!='discount' or fingerprint(payload)!=sha:
            raise ValueError('discount_controller_payload_changed')
        ref=load(folder/'discount-job.json');claim=load(folder/'claim.json')['claim_id']
        if ref['step']!='discount' or ref['job_id']!=fingerprint(['discount',claim]):
            raise ValueError('discount_job_not_original_claim')
        # This is deliberately status only. A finished job is never POSTed
        # again, and an unknown/running job cannot unlock the controller.
        job=edge.status(ref['job_id'])
        if (job.get('state')!='finished' or job.get('operation')!='discount'
                or job.get('job_id')!=ref['job_id'] or job.get('result',{}).get('claim',{}).get('claim_id')!=claim):
            raise ValueError('original_discount_has_no_finished_receipt')
        observation=persist(folder/'reconciled-discount-observation.json',job)
        result=reconcile_discount(authority,job,output_dir=folder)
        persist(folder.parent.parent/'terminals'/('discount-'+str(result['batch'])+'.json'),
                dict(result,bundle_id=payload['bundle']['bundle_id']))
        result=dict(result,action_id=action,request_sha=sha,reconciled_readonly=True,
                    observation=observation)
        evidence=persist(folder/'reconciled-result.json',result)
        store.recover(action,dict(result,evidence=evidence))
        return {'action_id':action,'job_id':ref['job_id'],'platform_write':False,'evidence':evidence}
    finally:store.close()


def persist_run_outcome(root,result):
    """The latest summary is replaceable; every prior summary is retained."""
    import os
    import uuid
    root=Path(root);target=root/'result.json';history=root/'result-history'
    persist(history/(fingerprint(result)+'.json'),result)
    if target.exists():
        old=load(target)
        if old==result:return str(target)
        persist(history/(fingerprint(old)+'.json'),old)
    temporary=root/('result-'+uuid.uuid4().hex+'.tmp')
    persist(temporary,result)
    os.replace(temporary,target)
    return str(target)

"""Reconcile an existing finished discount job; no browser write or new claim."""
from pathlib import Path

from campaign_continuous_flow import Store
from campaign_continuous_policy import fingerprint
from campaign_continuous_transport import persist
from campaign_discount_receipt import reconcile_discount
from campaign_edge_receipt import reconcile_signup
from campaign_entry_authority import load


def recover_finished_scope(root,authority,edge,*,artifact_roots):
    """Read original export status and reconcile only its idle scope action."""
    from campaign_continuous_transport import CampaignTransport
    root=Path(root);store=Store(root/'controller.sqlite3')
    try:
        rows=store.db.execute('SELECT a.id,a.step,a.payload_sha,a.status,r.owner FROM continuous_campaign_actions a '
            'JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.status<>?',('done',)).fetchall()
        if (len(rows)!=1 or rows[0][1]!='scope' or rows[0][3] not in ('unknown','interrupted_read')
                or rows[0][4] is not None):
            raise ValueError('only_one_idle_unfinished_scope_can_reconcile')
        action,_,sha,_,_=rows[0]
        folder=root/'shared'/'actions'/action
        saved=load(folder/'request.json');payload=saved['payload']
        if saved['step']!='scope' or fingerprint(payload)!=sha:
            raise ValueError('scope_controller_payload_changed')
        ref=load(folder/'product_export-job.json')
        if (ref['step']!='product_export' or ref['job_id']!=fingerprint(
                ['product_export',payload['identity']['shop_id'],action])):
            raise ValueError('scope_export_not_original_job')
        # No submit, inspection action, export or retry: status GET only.
        job=edge.status(ref['job_id'])
        if (job.get('state')!='finished' or job.get('operation')!='product_export'
                or job.get('job_id')!=ref['job_id']):
            raise ValueError('original_scope_export_has_no_finished_receipt')
        observation=persist(folder/'reconciled-product_export-observation.json',job)
        transport=CampaignTransport(edge,authority,root=root/'shared',request=load(root/'request.json'),
                                   artifact_roots=artifact_roots)
        # Reuses all normal SHA/page/SKU/count and ERP mapping checks. Original
        # unknown observation and request remain immutable, never overwritten.
        result=transport.finish_exported_scope(load(root/'shared'/'snapshot.json'),job,action,payload,folder)
        result=dict(result,action_id=action,request_sha=sha,status='terminal',
                    reconciled_readonly=True,observation=observation)
        evidence=persist(folder/'reconciled-scope-result.json',result)
        store.recover(action,dict(result,evidence=evidence))
        return {'action_id':action,'job_id':ref['job_id'],'platform_write':False,'evidence':evidence}
    finally:store.close()


def recover_finished_discount(root,authority,edge):
    return _recover_finished_phase(root,authority,edge,'discount',reconcile_discount)


def recover_finished_signup(root,authority,edge):
    return _recover_finished_phase(root,authority,edge,'signup',reconcile_signup)


def recover_finished_discount_window(root,authority,edge,*,artifact_roots):
    from campaign_continuous_transport import CampaignTransport
    root=Path(root);store=Store(root/'controller.sqlite3')
    try:
        rows=store.db.execute('SELECT a.id,a.step,a.payload_sha,a.status,r.owner FROM continuous_campaign_actions a '
            'JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.status<>?',('done',)).fetchall()
        if (len(rows)!=1 or rows[0][1]!='verify_discount_window' or rows[0][3]!='interrupted_read'
                or rows[0][4] is not None):raise ValueError('only_one_idle_discount_window_read_can_reconcile')
        action,_,sha,_,_=rows[0];folders=list((root/'segments').glob('*/actions/'+action))
        if len(folders)!=1:raise ValueError('discount_window_action_not_unique')
        folder=folders[0];saved=load(folder/'request.json');payload=saved['payload']
        if saved['step']!='verify_discount_window' or fingerprint(payload)!=sha:
            raise ValueError('discount_window_original_payload_changed')
        ref=load(folder/'discount_readback-job.json')
        if (ref.get('step')!='discount_readback' or ref['job_id']!=fingerprint(
                ['discount_readback',payload['identity']['shop_id'],action])):
            raise ValueError('discount_window_not_original_job')
        job=edge.status(ref['job_id'])
        if (job.get('state')!='finished' or job.get('operation')!='discount_readback'
                or job.get('job_id')!=ref['job_id']):raise ValueError('discount_window_original_job_not_finished')
        observation=persist(folder/'reconciled-discount_readback-observation.json',job)
        transport=CampaignTransport(edge,authority,root=folder.parent.parent,request={},artifact_roots=artifact_roots)
        result=transport.step_verify_discount_window(action,payload,folder,finished_job=job)
        if result.get('all_correct') is not True:raise ValueError('discount_window_readback_not_matching')
        result=dict(result,status='terminal',action_id=action,request_sha=sha,reconciled_readonly=True,observation=observation)
        evidence=persist(folder/'reconciled-result.json',result);store.recover(action,dict(result,evidence=evidence))
        return dict(action_id=action,job_id=ref['job_id'],platform_write=False,evidence=evidence)
    finally:store.close()


def _recover_finished_phase(root,authority,edge,phase,reconcile):
    root=Path(root);store=Store(root/'controller.sqlite3')
    try:
        rows=store.db.execute('SELECT a.id,a.step,a.payload_sha,a.status,r.owner FROM continuous_campaign_actions a '
            'JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.status<>?',('done',)).fetchall()
        if len(rows)!=1 or rows[0][1]!=phase or rows[0][3:]!=('unknown',None):
            raise ValueError('only_one_idle_unknown_'+phase+'_can_reconcile')
        action,_,sha,_,_=rows[0]
        folders=list((root/'segments').glob('*/actions/'+action))+list(root.glob('execution/actions/'+action))
        if len(folders)!=1:raise ValueError('discount_action_source_not_unique')
        folder=folders[0];saved=load(folder/'request.json');payload=saved['payload']
        if saved['step']!=phase or fingerprint(payload)!=sha:
            raise ValueError('discount_controller_payload_changed')
        ref=load(folder/(phase+'-job.json'));claim=load(folder/'claim.json')['claim_id']
        if ref['step']!=phase or ref['job_id']!=fingerprint([phase,claim]):
            raise ValueError('discount_job_not_original_claim')
        # This is deliberately status only. A finished job is never POSTed
        # again, and an unknown/running job cannot unlock the controller.
        job=edge.status(ref['job_id'])
        if (job.get('state')!='finished' or job.get('operation')!=phase
                or job.get('job_id')!=ref['job_id'] or job.get('result',{}).get('claim',{}).get('claim_id')!=claim):
            raise ValueError('original_discount_has_no_finished_receipt')
        observation=persist(folder/('reconciled-'+phase+'-observation.json'),job)
        result=reconcile(authority,job,output_dir=folder)
        persist(folder.parent.parent/'terminals'/(phase+'-'+str(result['batch'])+'.json'),
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

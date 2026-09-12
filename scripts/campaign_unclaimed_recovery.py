"""Release only a proven pre-claim checkpoint; never clear a platform claim."""
import json
from pathlib import Path
from campaign_entry_authority import load
from campaign_continuous_policy import fingerprint
from campaign_continuous_transport import persist
from campaign_submission_gate import validated_body


def recover(root,store,authority):
    root=Path(root)
    rows=store.db.execute("SELECT a.id,a.run_id,a.payload_sha,a.step,a.status,r.owner,r.body FROM continuous_campaign_actions a JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.status<>'done'").fetchall()
    if len(rows)!=1 or rows[0][3:6]!=('signup','unknown',None):
        raise ValueError('one_idle_preclaim_signup_required')
    action,run,sha=rows[0][:3];state=json.loads(rows[0][6])
    folders=list((root/'segments').glob('*/actions/'+action))
    if len(folders)!=1:raise ValueError('preclaim_action_folder_not_unique')
    folder=folders[0];request=load(folder/'request.json');payload=request['payload']
    if (request['step']!='signup' or fingerprint(payload)!=sha
            or sorted(p.name for p in folder.iterdir())!=['request.json']
            or sorted(state.get('pending') or [])!=sorted(payload['items'])
            or set(payload['items']).intersection(state.get('success',{}))):
        raise ValueError('preclaim_scope_or_artifact_changed')
    bundle=payload['bundle']['bundle_id']
    if authority.db.execute("SELECT 1 FROM attempts WHERE bundle_id=? AND phase='signup'",(bundle,)).fetchone():
        raise ValueError('existing_signup_claim_cannot_retry')
    body,_=validated_body(authority,bundle,'signup')
    if sorted({r['item'] for r in body['signup_rows']})!=sorted(payload['items']):
        raise ValueError('preclaim_validated_scope_changed')
    if (root/('process-unclaimed-signup-recovery-'+action+'.log')).exists():
        raise ValueError('preclaim_recovery_already_launched')
    evidence=persist(root/('unclaimed-signup-recovery-'+action+'.json'),
        {'action':list(rows[0]),'bundle_id':bundle,'claim_exists':False,'platform_write':False,
         'all_original_file_price_checks_passed':True,'request_sha':sha})
    store.db.execute('CREATE TABLE IF NOT EXISTS continuous_preclaim_recoveries(action_id TEXT PRIMARY KEY,evidence TEXT NOT NULL)')
    store.db.execute('BEGIN IMMEDIATE')
    try:
        if store.db.execute('SELECT 1 FROM continuous_campaign_runs WHERE id=? AND owner IS NOT NULL',(run,)).fetchone():
            raise ValueError('preclaim_controller_became_active')
        store.db.execute('INSERT INTO continuous_preclaim_recoveries VALUES(?,?)',(action,evidence))
        changed=store.db.execute("DELETE FROM continuous_campaign_actions WHERE id=? AND status='unknown' AND payload_sha=?",(action,sha)).rowcount
        if changed!=1:raise ValueError('preclaim_checkpoint_changed')
        store.db.execute('COMMIT')
    except BaseException:store.db.execute('ROLLBACK');raise
    return action

"""Validate the single interrupted local report, never a platform write retry."""
import json
from pathlib import Path
from campaign_continuous_policy import fingerprint
from campaign_entry_authority import load, file_sha


def validate(db, root):
    root=Path(root)
    rows=db.execute("SELECT a.id,a.step,a.status,a.payload_sha,r.owner,r.body FROM continuous_campaign_actions a JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.status<>'done'").fetchall()
    if len(rows)!=1 or rows[0][1:3]!=('report','interrupted_read') or rows[0][4] is not None:
        raise ValueError('one_idle_interrupted_report_required')
    aid,_,_,sha,_,body=rows[0]
    state=json.loads(body)
    if state.get('blocker')!={'step':'report','reason':'continuous_evidence_is_immutable'}:
        raise ValueError('exact_report_collision_required')
    diag=db.execute('SELECT code FROM continuous_campaign_diagnostics WHERE action_id=? ORDER BY event_id DESC LIMIT 1',(aid,)).fetchone()
    if diag!=('continuous_evidence_is_immutable',):raise ValueError('exact_report_collision_required')
    folders=list(root.glob('segments/*/actions/'+aid))
    if len(folders)!=1:raise ValueError('original_report_folder_not_unique')
    folder=folders[0];saved=load(folder/'request.json');p=saved['payload']
    if saved['step']!='report' or fingerprint(p)!=sha or (folder/'result.json').exists() or (folder/'claim.json').exists():
        raise ValueError('original_report_request_changed')
    segment=folder.parent.parent
    terminal=segment/'terminals'/(p['failed_phase']+'-'+str(p['batch'])+'.json')
    legacy=segment/'reports'/(str(p['batch'])+'.json')
    t,o=load(terminal),load(legacy)
    if (t.get('batch')!=p['batch'] or o.get('batch')!=p['batch']
            or t.get('bundle_id')!=o.get('bundle_id')
            or not p['items'] or {e['item'] for e in o['errors']}==set(p['items'])):
        raise ValueError('original_report_collision_not_proven')
    return dict(action_id=aid,request_sha=sha,terminal_path=str(terminal),terminal_sha256=file_sha(terminal),
                legacy_path=str(legacy),legacy_sha256=file_sha(legacy),claims_changed=False,platform_write=False)

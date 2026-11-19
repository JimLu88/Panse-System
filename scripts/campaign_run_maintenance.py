"""Review every existing run pin; resume only a proven post-reconciliation code gate."""
import json
from pathlib import Path
import re
import sqlite3
from campaign_entry_authority import load,file_sha
from campaign_continuous_policy import fingerprint
from campaign_scope_maintenance import differences


def owner_root(pin):
    pin=Path(pin).resolve()
    for parent in pin.parents:
        if re.fullmatch('[0-9a-f]{64}',parent.name) and (parent/'request.json').is_file():
            if fingerprint(load(parent/'request.json'))!=parent.name:raise ValueError('original_run_request_changed')
            return parent
    raise ValueError('exact_run_pin_owner_required')


def pins(root):
    root=Path(root).resolve();result={}
    for pin in root.rglob('execution-release.json'):
        relative=pin.relative_to(root).as_posix()
        if not re.fullmatch(r'(shared|segments/[0-9a-f]{64}|final-audit/[0-9a-f]{64}/[0-9a-f]{64})/execution-release.json',relative):
            raise ValueError('unreviewed_execution_pin_location')
        if owner_root(pin)!=root:raise ValueError('foreign_run_pin')
        result[relative]=load(pin)
    if 'shared/execution-release.json' not in result:raise ValueError('original_shared_pin_required')
    return result


def verify_checkpoint(root):
    root=Path(root).resolve();rid=root.name;request=load(root/'request.json')
    if fingerprint(request)!=rid or load(root.parent.parent/'requests'/(rid+'.json'))!=request:
        raise ValueError('original_run_request_changed')
    with sqlite3.connect((root.parent.parent/'jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT state,result FROM continuous_jobs WHERE id=?',(rid,)).fetchone()
        if (not row or row[0]!='unknown' or json.loads(row[1])!={
                'reason':'controller_did_not_produce_terminal_receipt','automatic_retry':False}
                or db.execute("SELECT 1 FROM continuous_jobs WHERE state='running'").fetchone()):
            raise ValueError('idle_local_release_failure_required')
    log=root/'process-discount-observation-recovery.log';text=log.read_text(encoding='utf-8')
    first=json.loads(text.splitlines()[0]).get('discount_reconciliation') or {}
    if (not text.rstrip().endswith('ValueError: pinned_execution_release_changed_restore_original_version')
            or 'in execute_request' not in text or 'in __init__' not in text
            or first.get('platform_write') is not False
            or (root/'reconciled-release-resume.json').exists()):
        raise ValueError('proven_post_reconciliation_release_failure_required')
    aid=first.get('action_id');jid=first.get('job_id')
    if any(not re.fullmatch('[0-9a-f]{64}',str(v)) for v in (aid,jid)):
        raise ValueError('original_reconciled_identity_required')
    with sqlite3.connect((root/'controller.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        if (db.execute("SELECT 1 FROM continuous_campaign_actions WHERE status<>'done'").fetchone()
                or db.execute('SELECT 1 FROM continuous_campaign_runs WHERE owner IS NOT NULL').fetchone()
                or db.execute('SELECT 1 FROM continuous_campaign_shop_owners').fetchone()):
            raise ValueError('unfinished_or_active_action_protected')
        action=db.execute('SELECT id,step,payload_sha,result FROM continuous_campaign_actions ORDER BY rowid DESC LIMIT 1').fetchone()
        recovery=db.execute('SELECT receipt_sha,receipt FROM continuous_campaign_recoveries WHERE action_id=?',(aid,)).fetchone()
        if not action or action[:2]!=(aid,'discount') or not recovery:
            raise ValueError('last_reconciled_discount_required')
        result=json.loads(action[3])
        if json.loads(recovery[1])!=result or recovery[0]!=fingerprint(result):
            raise ValueError('reconciled_action_audit_changed')
    folders=list(root.glob('segments/*/actions/'+aid))
    if len(folders)!=1:raise ValueError('original_discount_folder_not_unique')
    folder=folders[0];saved=load(folder/'reconciled-result.json')
    if (first.get('evidence')!=str((folder/'reconciled-result.json').resolve())
            or result!=dict(saved,evidence=first['evidence'])
            or saved.get('request_sha')!=action[2] or saved.get('reconciled_readonly') is not True
            or saved.get('status')!='terminal'):
        raise ValueError('completed_discount_receipt_changed')
    observed=Path(saved['observation']).resolve(strict=True)
    if not observed.is_relative_to(folder.resolve()):raise ValueError('foreign_discount_observation')
    job=load(observed);terminal=job.get('result') or {}
    if (job.get('job_id')!=jid or job.get('operation')!='discount' or job.get('state')!='finished'
            or terminal.get('state')!='verified_partial_offer_terminal' or terminal.get('platform_write') is not False
            or terminal.get('success',0)<1 or len(terminal.get('failure_rows',[]))!=terminal.get('failed')):
        raise ValueError('verified_partial_discount_required')
    official=Path(terminal['evidence_path']);feedback=Path(terminal['feedback']['path'])
    if (load(official)!={k:v for k,v in terminal.items() if k not in ('evidence_path','recording')}
            or file_sha(feedback)!=terminal['feedback']['sha256']):
        raise ValueError('official_partial_receipt_changed')
    refs=[root/'request.json',root.parent.parent/'requests'/(rid+'.json'),root/'time-request.json',
          log,root/'discount-observation-recovery.json',folder/'reconciled-result.json',Path(saved['evidence']),
          observed,official,feedback]
    return dict(schema='campaign_reconciled_release_checkpoint_v1',request_id=rid,action_id=aid,job_id=jid,
                success=terminal['success'],failed=terminal['failed'],action_state='done',
                references=[dict(path=str(p),sha256=file_sha(p)) for p in refs],platform_write=False,claims_released=False)


def validate_document(checkpoint,body,doc):
    checkpoint=Path(checkpoint).resolve();root=owner_root(checkpoint);relative=checkpoint.relative_to(root).as_posix()
    old=load(checkpoint);review=doc['review'];manifest=load(review['path'])
    if (doc.get('schema')!='campaign_run_maintenance_v1' or doc.get('pin_relative')!=relative
            or doc.get('old_release')!=old or doc.get('new_release')!=body
            or doc.get('checkpoint',{}).get('schema')!='campaign_reconciled_release_checkpoint_v1'
            or doc['checkpoint'].get('request_id')!=root.name or manifest.get('request_id')!=root.name
            or fingerprint(old['source_hashes'])!=old['release_id']
            or fingerprint(body['source_hashes'])!=body['release_id']
            or file_sha(review['path'])!=review['sha256']):
        raise ValueError('run_maintenance_identity_changed')
    entry=manifest.get('pins',{}).get(relative)
    actual=differences(old['source_hashes'],body['source_hashes'])
    if entry!={'old_release_id':old['release_id'],'changes':actual} or not actual:
        raise ValueError('run_pin_changes_not_reviewed')
    if any(not p.endswith('.py') or '/campaign_' not in p.replace('\\','/') for p in actual):
        raise ValueError('business_rules_not_maintenance')
    if not manifest.get('validation_evidence'):raise ValueError('run_validation_required')
    for ref in [*manifest['validation_evidence'],*doc['checkpoint']['references']]:
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('run_maintenance_evidence_changed')
    for sha in old['source_hashes'].values():
        if file_sha(checkpoint.parent/'execution-releases'/old['release_id']/sha)!=sha:
            raise ValueError('original_pin_archive_changed')
    return doc


def approve_all(root,review_path):
    from campaign_execution_release import sources
    root=Path(root).resolve();proof=verify_checkpoint(root);inventory=pins(root)
    review_path=Path(review_path).resolve(strict=True);review=load(review_path)
    if set(review.get('pins',{}))!=set(inventory):raise ValueError('all_run_pins_must_be_reviewed')
    current=sources();body=dict(schema='campaign_execution_release_v1',release_id=fingerprint(current),
                              source_hashes=current,business_identity_changed=False)
    docs=[]
    for relative,old in inventory.items():
        pin=root/relative
        doc=dict(schema='campaign_run_maintenance_v1',pin_relative=relative,old_release=old,new_release=body,
                 checkpoint=proof,review=dict(path=str(review_path),sha256=file_sha(review_path)),
                 original_pin_preserved=True,platform_write=False,claims_released=False)
        validate_document(pin,body,doc);docs.append((pin,doc))
    result=[]
    for pin,doc in docs:
        target=pin.parent/'execution-maintenance'/(body['release_id']+'.json');target.parent.mkdir(exist_ok=True)
        if target.exists():
            if load(target)!=doc:raise ValueError('immutable_run_maintenance_receipt')
        else:
            with target.open('x',encoding='utf-8') as f:json.dump(doc,f,ensure_ascii=False,indent=2)
        result.append(dict(pin=str(pin),approval=str(target),sha256=file_sha(target)))
    return dict(request_id=root.name,release_id=body['release_id'],pins=result,platform_write=False)


def inspect_resume(root):
    from campaign_execution_release import sources
    from campaign_scope_maintenance import validate
    root=Path(root);proof=verify_checkpoint(root);current=sources()
    body=dict(schema='campaign_execution_release_v1',release_id=fingerprint(current),source_hashes=current,business_identity_changed=False)
    for relative in pins(root):
        doc=validate(root/relative,body)
        if doc.get('checkpoint')!=proof:raise ValueError('run_checkpoint_changed_since_review')
    return proof,body['release_id']

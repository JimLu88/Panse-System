"""Explicit, append-only maintenance approval for a stopped scope checkpoint."""
import json
from pathlib import Path
from campaign_continuous_policy import fingerprint
from campaign_entry_authority import load,file_sha
from campaign_scope_checkpoint import verify,check_references


def differences(old,new):
    return {p:dict(before=old.get(p),after=new.get(p)) for p in sorted(set(old)|set(new)) if old.get(p)!=new.get(p)}


def validate(checkpoint,body):
    checkpoint=Path(checkpoint)
    path=checkpoint.parent/'execution-maintenance'/(body['release_id']+'.json')
    doc=load(path)
    return validate_document(checkpoint,body,doc)


def validate_document(checkpoint,body,doc):
    checkpoint=Path(checkpoint)
    old=load(checkpoint)
    if (doc.get('schema')!='campaign_scope_maintenance_v1' or doc.get('new_release')!=body
            or doc.get('old_release')!=old or fingerprint(old['source_hashes'])!=old['release_id']
            or fingerprint(body['source_hashes'])!=body['release_id']
            or doc['checkpoint']['request_id']!=checkpoint.parent.parent.name):
        raise ValueError('scope_maintenance_release_mismatch')
    review=doc['review']
    if file_sha(review['path'])!=review['sha256']:raise ValueError('scope_maintenance_review_changed')
    manifest=load(review['path'])
    actual=differences(old['source_hashes'],body['source_hashes'])
    if (not actual or manifest.get('changes')!=actual or manifest.get('request_id')!=doc['checkpoint']['request_id']
            or manifest.get('old_release_id')!=old['release_id']):
        raise ValueError('scope_maintenance_unreviewed_source_drift')
    if (not manifest.get('validation_evidence') or
            any(not p.endswith('.py') or '/campaign_' not in p.replace('\\','/') for p in actual)):
        raise ValueError('exact_reviewed_scope_patch_required')
    for ref in manifest['validation_evidence']:
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('scope_maintenance_validation_changed')
    check_references(doc['checkpoint'])
    for filename,sha in old['source_hashes'].items():
        archive=checkpoint.parent/'execution-releases'/old['release_id']/sha
        if file_sha(archive)!=sha:raise ValueError('scope_maintenance_original_archive_changed')
    return doc


def approve(root,request_id,review_path,*,phase='scope'):
    from campaign_execution_release import sources
    root=Path(root);pin=root/'shared/execution-release.json';old=load(pin)
    if phase=='scope':proof=verify(root,request_id)
    elif phase=='discount':
        from campaign_scope_checkpoint import verify_discount
        proof=verify_discount(root,request_id)
    else:raise ValueError('unsupported_maintenance_checkpoint')
    review_path=Path(review_path).resolve(strict=True);review=load(review_path)
    current=sources();new=dict(schema='campaign_execution_release_v1',release_id=fingerprint(current),
                              source_hashes=current,business_identity_changed=False)
    if (review.get('request_id')!=request_id or review.get('old_release_id')!=old['release_id']
            or review.get('changes')!=differences(old['source_hashes'],current)
            or not review.get('validation_evidence')):
        raise ValueError('exact_reviewed_scope_patch_required')
    if any(not p.endswith('.py') or '/campaign_' not in p.replace('\\','/') for p in review['changes']):
        raise ValueError('business_rule_change_not_scope_maintenance')
    doc=dict(schema='campaign_scope_maintenance_v1',old_release=old,new_release=new,checkpoint=proof,
             review=dict(path=str(review_path),sha256=file_sha(review_path)),platform_write=False,
             old_release_preserved=True,request_changed=False,claims_released=False)
    validate_document(pin,new,doc)
    path=pin.parent/'execution-maintenance'/(new['release_id']+'.json');path.parent.mkdir(exist_ok=True)
    if path.exists():
        if load(path)!=doc:raise ValueError('scope_maintenance_receipt_is_immutable')
    else:
        with path.open('x',encoding='utf-8') as f:json.dump(doc,f,ensure_ascii=False,sort_keys=True,indent=2)
    validate(pin,new)
    return dict(path=str(path),sha256=file_sha(path),request_id=request_id,platform_write=False)

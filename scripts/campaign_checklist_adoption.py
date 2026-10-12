"""Attach verified local checklist evidence to an immutable legacy bundle.

No bundle rewrite, file regeneration, claim release, submission or source refresh.
Historical dispatched effects keep their original receipt-only recovery path.
"""
import json
from pathlib import Path
from campaign_entry_authority import file_sha, load
from campaign_price_snapshot import digest


def receipt_path(authority, bundle):
    import re
    if not re.fullmatch('[0-9a-f]{64}',bundle):raise ValueError('invalid_bundle_identity')
    return authority.path.parent/'checklist-adoptions'/(bundle+'.json')


def augment(authority,bundle,body,snapshot,bases,*,candidate_scope=None):
    from campaign_submission_checklist_contract import materialize, verify_contract
    if body.get('submission_checklist'):
        verify_contract(body,snapshot,bases)
        return body
    if candidate_scope is None:
        path=receipt_path(authority,bundle)
        if not path.is_file():raise ValueError('legacy_bundle_checklist_adoption_required')
        receipt=load(path)
        if receipt.get('bundle_id')!=bundle or receipt.get('original_body_sha256')!=digest(body):
            raise ValueError('legacy_checklist_original_bundle_changed')
        candidate_scope=receipt['scope_source']
    else:receipt=None
    value=dict(body,checklist_scope_source=candidate_scope)
    contract,issues=materialize(value,snapshot,bases)
    if issues:raise ValueError('legacy_checklist_inputs_invalid:'+issues[0]['error'])
    if receipt is not None and receipt.get('submission_checklist')!=contract:
        raise ValueError('legacy_checklist_source_binding_changed')
    return dict(value,submission_checklist=contract)


def adopt_existing(authority,bundle,scope_path):
    """One bounded local migration. Caller must own an idle request checkpoint."""
    from campaign_submission_gate import validated_body
    scope_path=Path(scope_path)
    scope=dict(path=str(scope_path.resolve()),sha256=file_sha(scope_path))
    authority.db.execute('BEGIN IMMEDIATE')
    try:
        original=authority.get_bundle(bundle)
        attempts=list(authority.db.execute('SELECT id,status FROM attempts WHERE bundle_id=? AND phase=?',(bundle,'signup')))
        for attempt in attempts:
            claim=attempt['id'].rsplit(':',1)[0]
            binding=authority.db.execute('SELECT state FROM claim_transports WHERE claim_id=?',(claim,)).fetchone()
            if attempt['status']!='unknown' or not binding or binding[0]!='claimed_not_dispatched':
                raise ValueError('legacy_effect_requires_original_readonly_recovery')
        body,_=validated_body(authority,bundle,'signup',_candidate_scope=scope)
        receipt=dict(schema='campaign_checklist_adoption_v1',bundle_id=bundle,
            original_body_sha256=digest(original),scope_source=scope,
            submission_checklist=body['submission_checklist'],platform_write=False,
            regenerated_files=False,claims_changed=False)
        path=receipt_path(authority,bundle)
        if path.exists():
            if load(path)!=receipt:raise ValueError('legacy_checklist_adoption_changed')
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x',encoding='utf-8') as out:json.dump(receipt,out,ensure_ascii=False,indent=2)
        authority.db.execute('COMMIT')
        return dict(receipt,receipt_path=str(path))
    except BaseException:
        authority.db.execute('ROLLBACK');raise


def main():
    import argparse
    from campaign_entry_authority import Authority
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',required=True);p.add_argument('--scope',type=Path,required=True)
    args=p.parse_args();authority=Authority()
    try:print(json.dumps(adopt_existing(authority,args.bundle,args.scope),ensure_ascii=False))
    finally:authority.close()


if __name__=='__main__':main()

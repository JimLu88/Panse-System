"""Opt-in controlled submission adapter. Raw browser/API calls are NOT intercepted.

03 supplies its existing single-call transport; this module never connects a
browser. Claim is committed before callback; timeout/exception stays unknown.
"""
from decimal import Decimal
from pathlib import Path
from campaign_entry_authority import Authority, file_sha, load, validate_price
from campaign_generate_current_files import build_rows
from campaign_official_template import discount_rate, template_rows, fill_selected_rows, fill_single_discount_rows
from campaign_discount_template import load_fixed_discount_template


def validated_body(authority, identity, phase):
    body=authority.get_bundle(identity)
    from campaign_scoped_tolerance import validate_bundle_policy
    validate_bundle_policy(body)
    if body['rule_sha256']!=authority.rule_sha:raise ValueError('rule_version_changed')
    if file_sha(body['snapshot_path'])!=body['snapshot_sha256']:raise ValueError('snapshot_file_changed')
    if file_sha(body['template_path'])!=body['template_sha256']:raise ValueError('official_template_changed')
    snapshot=authority.resolve_snapshot(load(body['snapshot_path']))
    if snapshot['resolved_price_version_sha256']!=body['price_version'] or snapshot['entry_source_sha256']!=body['entry_source_sha256']:
        raise ValueError('mapping_or_price_authority_changed_regenerate_local_files')
    bases=authority.bases(snapshot)
    raw=Path(body['template_path']).read_bytes()
    rows,discounts,issues=build_rows(snapshot,template_rows(raw),discount_rate(body['official_rate']),body['target'],bases,set(body['signup_items']),set(body['discount_items']))
    if issues:raise ValueError('generation_inputs_no_longer_valid')
    expected={(r['item'],r['sku']):r for r in rows}
    if len(expected)!=len(body['signup_rows']) or set(expected)!={(r['item'],r['sku']) for r in body['signup_rows']}:
        raise ValueError('signup_scope_changed')
    for row in body['signup_rows']:
        pair=row['item'],row['sku'];original=expected[pair]
        if row['custom']!=original['custom'] or row['erp_code']!=original['erp_code']:raise ValueError('classification_or_mapping_changed')
        if not row['custom'] and any(row.get(k)!=original.get(k) for k in ('target','big_target')):
            raise ValueError('frozen_targets_changed')
        evidence=row.get('lowering_evidence')
        authorized=failed=False
        if evidence:
            auth=load(evidence['authorization_path']);failure=load(evidence['failure_path'])
            authorized=file_sha(evidence['authorization_path'])==evidence['authorization_sha256'] and auth.get('campaign')==body['campaign'] and any(
                (x.get('item'),x.get('sku'),str(x.get('activity_price')))==(*pair,str(row['activity_price'])) for x in auth.get('authorized_custom_prices',[]))
            failed=file_sha(evidence['failure_path'])==evidence['failure_sha256'] and failure.get('campaign')==body['campaign'] and failure.get('terminal') is True and bool(failure.get('batch_id')) and any(
                (x.get('item'),x.get('sku'),x.get('status'))==(*pair,'failed') for x in failure.get('rows',[]))
        validate_price(row,original['activity_price'],bases.get(pair),lowering_authorized=authorized,failed_exact=failed)
    from campaign_discount_reuse import reconcile
    if discounts!=body['planned_discount_rows']:raise ValueError('planned_discount_rows_changed')
    discounts,reuse,issues=reconcile(body['signup_rows'],discounts,authority.discount_offers(),body['start'],body['end'],
                                    discount_rate(body['official_rate']),excluding_offer='bundle:'+identity,campaign=body['campaign'],target=body['target'])
    if issues:raise ValueError('actual_discount_reuse_invalid:'+issues[0]['error'])
    if reuse!=body.get('discount_reuse',[]):raise ValueError('discount_reuse_evidence_changed')
    if discounts!=body['discount_rows']:raise ValueError('discount_formula_changed')
    name={'signup':'活动报名.xlsx','discount':'单品立减.xlsx'}[phase]
    files=[f for f in body['files'] if Path(f['path']).name==name]
    if len(files)!=1:raise ValueError('phase_file_missing_or_ambiguous')
    file=files[0]
    if file_sha(file['path'])!=file['sha256']:raise ValueError('upload_file_changed')
    # Do not trust a self-edited receipt hash: reproduce bytes from authority rows.
    reproduced=(fill_selected_rows(raw,body['signup_rows'],official_rate=body['official_rate']) if phase=='signup'
                else fill_single_discount_rows(load_fixed_discount_template(),body['discount_rows']))
    if reproduced!=Path(file['path']).read_bytes():raise ValueError('upload_bytes_not_authoritative_generation')
    return body,file


def run_once(bundle_id, phase, submit_callback, *, authority=None):
    owned=authority is None
    authority=authority or Authority()
    try:
        body,file=validated_body(authority,bundle_id,phase)
        claim=authority.claim(bundle_id,phase)
        # Callback gets the exact validated file and campaign/window; must check
        # its currently selected page and return a true official terminal receipt.
        result=submit_callback(dict(file=file,campaign=body['campaign'],phase=phase,
                                    start=body['start'],end=body['end'],claim_id=claim))
        authority.terminal(claim,result or {})
        return {'claim_id':claim,'result':result,'automatic_retry':False}
    finally:
        if owned:authority.close()


def main():
    """Process-separated owner transport: claim before write, record after terminal."""
    import argparse
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    claim=sub.add_parser('claim')
    claim.add_argument('--bundle',required=True)
    claim.add_argument('--phase',choices=['discount','signup'],required=True)
    record=sub.add_parser('record')
    record.add_argument('--claim',required=True)
    record.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args();authority=Authority()
    try:
        if args.command=='claim':
            body,file=validated_body(authority,args.bundle,args.phase)
            claim_id=authority.claim(args.bundle,args.phase)
            print(json.dumps(dict(claim_id=claim_id,file=file,campaign=body['campaign'],phase=args.phase,
                                  start=body['start'],end=body['end'],state='unknown_until_official_terminal',
                                  platform_write=False,automatic_retry=False),ensure_ascii=False))
        else:
            receipt=load(args.receipt)
            authority.terminal(args.claim,dict(receipt,evidence_path=str(args.receipt.resolve())))
            states=[dict(r) for r in authority.db.execute('SELECT item,status FROM attempts WHERE id LIKE ?',(args.claim+':%',))]
            print(json.dumps(dict(claim_id=args.claim,states=states,platform_write=False),ensure_ascii=False))
    finally:authority.close()


if __name__=='__main__':main()

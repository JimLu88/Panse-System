"""01 single local generation from one price snapshot and current official templates.

No network/browser/ERP writes. Unknown prices/mappings are reported once,
not guessed or silently excluded. Does not authorize upload or a new discount.
"""
import argparse
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

from campaign_official_template import discount_rate, fill_selected_rows, fill_single_discount_rows, money, template_rows
from campaign_price_snapshot import digest
from campaign_reserve_policy import fixed_basis, inherit_basis
from campaign_discount_template import FIXED_TEMPLATE, load_fixed_discount_template

SUCCESS = {'活动中','进行中','已生效','已发布设定'}


def official_cut(daily, rate):
    # Match campaign_service._discount_row_for_sale: existing platform evidence
    # uses whole-yuan ceiling at ordinary prices, exact cents below 100 yuan.
    exact = daily * rate
    if daily < Decimal('100'):
        return exact.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return exact.to_integral_value(rounding=ROUND_CEILING)


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_bases(paths):
    # Shared receipt adapter: no two-source allowlist or fixed coverage count.
    # First-pass generation still accepts no basis. Historical daily is not fixed.
    from campaign_price_basis import receipt_records
    bases = {}
    for path in paths:
        raw = path.read_bytes()
        for record in receipt_records(json.loads(raw.decode('utf-8-sig')), path):
            key = record['item'], record['sku']
            basis = dict(record['basis'], source_sha256=sha(raw))
            if key in bases and (
                Decimal(bases[key]['original']) != Decimal(basis['original'])
                or bases[key].get('erp_code') != basis.get('erp_code')
            ):
                raise ValueError('conflicting_fixed_custom_basis:' + '/'.join(key))
            bases[key] = basis
    return bases


def build_rows(snapshot, identities, rate, target, bases, signup_items=None, discount_items=None):
    erp = snapshot['all_erp_rows']
    if digest(erp) != snapshot['resolved_price_version_sha256']:
        raise ValueError('price_snapshot_changed')
    index = {}
    for row in erp:
        item_ids = set(str(x) for x in [row.get('item'),row.get('product_item_id'),*(row.get('product_alt_item_ids') or [])] if x)
        for item in item_ids:
            for sku in set([str(row.get('sku') or ''), *map(str,row.get('alt') or [])]):
                if sku:
                    index.setdefault((item,sku), []).append(row)
    activity, discounts, issues = [], [], []
    successful_items = {row['item'] for row in identities if row['state'] in SUCCESS}
    present_items = {row['item'] for row in identities}
    for selected in (signup_items,discount_items):
        if selected is not None and not selected.issubset(present_items):
            raise ValueError('explicit_scope_item_not_in_current_template')
    pairs = set()
    for identity in identities:
        pair = identity['item'], identity['sku']
        if pair in pairs:
            raise ValueError('duplicate_current_template_pair')
        pairs.add(pair)
        row_common = dict(item=pair[0],sku=pair[1])
        needs_signup = pair[0] not in successful_items and (signup_items is None or pair[0] in signup_items)
        needs_discount = discount_items is None or pair[0] in discount_items
        if not needs_signup and not needs_discount:
            continue
        matches = index.get(pair, [])
        if len(matches) != 1:
            issues.append(dict(**row_common,error='erp_mapping_missing_or_not_unique',matches=len(matches)))
            continue
        row = matches[0]
        row_common['erp_code'] = row['code']
        if row.get('custom') not in (True,False):
            issues.append(dict(**row_common,error='custom_classification_unknown'))
            continue
        # Successful activity rows are not resubmitted, but ordinary new-window
        # discounts must still cover them. Custom SKUs do not get single discounts.
        if row['custom'] and not needs_signup:
            continue
        try:
            daily = money(row['daily'])
            if daily <= 0:
                raise ValueError('daily_price_missing_or_nonpositive')
            if row['custom']:
                basis = bases.get(pair)
                if basis is not None and basis.get('lineage') and basis['lineage'][-1]['erp_code'] != row['code']:
                    raise ValueError('lineage_erp_code_not_current_snapshot')
                if basis is not None and daily < Decimal(basis['floor']):
                    raise ValueError('current_daily_below_fixed_custom_floor_requires_user_decision')
                activity.append(dict(**row_common,activity_price=str(daily),custom=True,custom_basis=basis,price_action='keep_current_erp_daily_no_lowering',basis_required_before_lowering=True))
                continue
            goal, big = money(row[target+'_target']), money(row['big_target'])
            cut = official_cut(daily, rate)
            deduct = daily-cut-goal
            if goal <= 0 or big <= 0 or goal < big or deduct < 0 or daily-cut-deduct != goal:
                raise ValueError('price_formula_cannot_meet_frozen_target')
            if needs_signup:
                activity.append(dict(**row_common,activity_price=str(daily),custom=False,target=str(goal),big_target=str(big)))
            if needs_discount and deduct > 0:
                discounts.append(dict(**row_common,deduct=str(deduct),daily=str(daily),official_cut=str(cut),target=str(goal),big_target=str(big),final=str(goal),custom=False))
        except (KeyError, ValueError, TypeError) as exc:
            issues.append(dict(**row_common,error=str(exc)))
    return activity, discounts, issues


def generate(args):
    from campaign_entry_authority import Authority
    authority=Authority()
    try:
        return _generate(args,authority)
    finally:
        authority.close()


def _generate(args,authority):
    if args.output_dir.exists():
        raise ValueError('output_exists_do_not_overwrite_or_replay')
    start, end = (datetime.strptime(x,'%Y-%m-%d %H:%M:%S') for x in (args.start,args.end))
    if end <= start:
        raise ValueError('invalid_exact_window')
    from campaign_entry_authority import exact_campaign, validate_price
    campaign = exact_campaign(getattr(args, 'campaign_key', None))
    raw = args.activity_template.read_bytes()
    rate = discount_rate(args.official_rate)
    signup_items = set(args.signup_items.split(',')) if args.signup_items is not None else None
    discount_items = set(args.discount_items.split(',')) if args.discount_items is not None else None
    # Persist explicitly supplied sources, so the next task need not remember flags.
    for path in args.custom_basis_receipt:
        authority.register_source(path,'fixed',sha(path.read_bytes()))
    snapshot = authority.resolve_snapshot(load(args.snapshot))
    bases=authority.bases(snapshot)
    identities=template_rows(raw)
    blocked_signup=authority.blocked(campaign,'signup',args.start,args.end)
    blocked_discount=authority.blocked(campaign,'discount',args.start,args.end)
    present={r['item'] for r in identities}
    signup_items=(present if signup_items is None else signup_items)-blocked_signup.keys()
    discount_items=present if discount_items is None else discount_items
    activity, discounts, issues = build_rows(snapshot,identities,rate,args.target,bases,signup_items,discount_items)
    corrections=load(args.custom_corrections) if getattr(args,'custom_corrections',None) else {'rows':[]}
    by_pair={(r['item'],r['sku']):r for r in activity}
    seen=set()
    for correction in corrections['rows']:
        pair=correction['item'],correction['sku']
        if pair in seen:raise ValueError('duplicate_custom_correction')
        seen.add(pair)
        row=by_pair.get(pair)
        if row is None:raise ValueError('custom_correction_outside_current_unsuccessful_scope')
        daily=row['activity_price']
        candidate=dict(row,activity_price=correction['activity_price'])
        # Authorizations and failure evidence are exact immutable local receipts,
        # never a broad flag inferred from an AI recommendation or ceiling.
        auth=load(Path(correction['authorization_path']))
        failure=load(Path(correction['failure_path']))
        authorized=sha(Path(correction['authorization_path']).read_bytes())==correction['authorization_sha256'] and auth.get('campaign')==campaign and any(
            (x.get('item'),x.get('sku'),str(x.get('activity_price')))==(*pair,str(candidate['activity_price'])) for x in auth.get('authorized_custom_prices',[]))
        failed=sha(Path(correction['failure_path']).read_bytes())==correction['failure_sha256'] and failure.get('campaign')==campaign and failure.get('terminal') is True and bool(failure.get('batch_id')) and any(
            (x.get('item'),x.get('sku'),x.get('status'))==(*pair,'failed') for x in failure.get('rows',[]))
        try:
            validate_price(candidate,daily,bases.get(pair),lowering_authorized=authorized,failed_exact=failed)
            row.update(activity_price=candidate['activity_price'],price_action='authorized_failed_custom_lowering',lowering_evidence=correction)
        except ValueError as exc:issues.append(dict(item=pair[0],sku=pair[1],error=str(exc)))
    from campaign_discount_reuse import reconcile
    from campaign_scoped_tolerance import policy_for
    continuous_rule_sha = getattr(args, 'continuous_rule_sha', None)
    tolerance = policy_for(campaign,args.start,args.end,rate,args.target,continuous_rule_sha=continuous_rule_sha)
    planned_discounts=discounts
    discounts,reused,reuse_issues=reconcile(activity,discounts,authority.discount_offers(),args.start,args.end,rate,campaign=campaign,target=args.target,continuous_rule_sha=continuous_rule_sha)
    issues.extend(reuse_issues)
    result = dict(status='local_input_issues' if issues else 'local_files_ready_not_uploaded',platform_write=False,database_write=False,automatic_retry=False,price_version=snapshot['resolved_price_version_sha256'],official_rate=str(rate),target=args.target,window={'start':args.start,'end':args.end,'timezone':'Asia/Shanghai'},activity_rows=activity,discount_rows=discounts,discount_reuse=reused,issues=issues,activity_template_sha256=sha(raw),files=[],note='Registered local evidence only, not a platform preflight or fresh readback. Actual reused amounts must meet frozen targets; no inherited tolerance. No upload files on issues. Business database untouched; local authority persisted.')
    result['explicit_signup_items'] = sorted(signup_items) if signup_items is not None else None
    result['explicit_discount_items'] = sorted(discount_items) if discount_items is not None else None
    result['campaign']=campaign
    result['final_price_tolerance']=tolerance
    if tolerance:
        result['note']='Current pinned campaign-only user tolerance applied to actual reused discounts; not a daily-price change or automatic minus-two adjustment. No upload files on other issues; no platform preflight.'
    result['protected_scope']={'signup':blocked_signup,'discount':blocked_discount}
    outputs = []
    if not issues:
        if activity:
            outputs.append(('活动报名.xlsx',fill_selected_rows(raw,activity,official_rate=args.official_rate)))
        if discounts:
            discount_path = getattr(args, 'discount_template', None) or FIXED_TEMPLATE
            discount_raw = load_fixed_discount_template(discount_path)
            result['discount_template_sha256'] = sha(discount_raw)
            result['discount_template_source'] = str(discount_path)
            result['discount_template_policy'] = 'fixed_user_master_no_redownload_no_filled_batch_reuse'
            outputs.append(('单品立减.xlsx',fill_single_discount_rows(discount_raw,discounts)))
    args.output_dir.mkdir(parents=True,exist_ok=False)
    for name, content in outputs:
        path = args.output_dir/name
        with path.open('xb') as stream:
            stream.write(content)
        result['files'].append(dict(path=str(path.resolve()),sha256=sha(content)))
    if not issues:
        body=dict(campaign=campaign,start=args.start,end=args.end,rule_sha256=authority.rule_sha,final_price_tolerance=tolerance,
                  snapshot_path=str(args.snapshot.resolve()),snapshot_sha256=sha(args.snapshot.read_bytes()),
                  price_version=snapshot['resolved_price_version_sha256'],entry_source_sha256=snapshot['entry_source_sha256'],
                  template_path=str(args.activity_template.resolve()),template_sha256=sha(raw),
                  official_rate=args.official_rate,target=args.target,signup_items=sorted(signup_items),discount_items=sorted(discount_items),
                  signup_rows=activity,discount_rows=discounts,planned_discount_rows=planned_discounts,discount_reuse=reused,corrections=corrections,files=result['files'])
        if continuous_rule_sha is not None:
            body['continuous_rule_sha']=continuous_rule_sha
        result['entry_bundle_id']=authority.save_bundle(body)
        result['submission_entry']='campaign_submission_gate.run_once'
    with (args.output_dir/'receipt.json').open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--campaign-key',required=True,help='Exact campaignId/unitedActivityId/signRecordId, not title or template filename')
    parser.add_argument('--custom-corrections',type=Path,help='Only current failed exact custom SKUs with pinned authorization/failure receipts; no ordinary price overrides')
    parser.add_argument('--continuous-rule-sha',help='Explicit approved continuous policy for new bundles only; omitted preserves historical rules')
    parser.add_argument('--activity-template',type=Path,required=True)
    parser.add_argument('--discount-template',type=Path,default=FIXED_TEMPLATE,help='Optional byte-identical local copy of the fixed single-discount master; never download per campaign')
    parser.add_argument('--official-rate',required=True)
    parser.add_argument('--target',choices=['medium','big'],required=True)
    parser.add_argument('--start',required=True)
    parser.add_argument('--end',required=True)
    parser.add_argument('--custom-basis-receipt',type=Path,action='append',default=[])
    parser.add_argument('--signup-items',help='Explicit comma-separated whole-item scope decided by 01; omit for all incomplete template items')
    parser.add_argument('--discount-items',help='Independent explicit comma-separated new-window discount item scope; omit for all template items, including already enrolled')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    result=generate(args)
    print(json.dumps({k:v for k,v in result.items() if k not in ('activity_rows','discount_rows','issues','note')},ensure_ascii=False))
    print(json.dumps({'issues_count':len(result['issues']),'issues':result['issues']},ensure_ascii=False))
    raise SystemExit(2 if result['issues'] else 0)

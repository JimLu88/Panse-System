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
    bases = {}
    for path in paths:
        receipt = load(path)
        for row in receipt.get('rows', []):
            if 'fixed_original_record' not in row or 'fixed_floor' not in row:
                continue
            original, floor = money(row['fixed_original_record']), Decimal(str(row['fixed_floor']))
            if original <= 0 or floor != original * Decimal('.20'):
                raise ValueError('invalid_fixed_custom_basis:' + str(path))
            key = str(row['item']), str(row['sku'])
            basis = dict(original=str(original), floor=str(floor), source=str(path), source_sha256=sha(path.read_bytes()), provenance=row.get('floor_provenance') or row.get('rotation_source') or 'explicit_fixed_original_record', first_ever_historical_record_claimed=row.get('first_ever_historical_record_claimed'))
            if key in bases and bases[key]['original'] != basis['original']:
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
                discounts.append(dict(**row_common,deduct=str(deduct),daily=str(daily),official_cut=str(cut),target=str(goal),final=str(goal),custom=False))
        except (KeyError, ValueError, TypeError) as exc:
            issues.append(dict(**row_common,error=str(exc)))
    return activity, discounts, issues


def generate(args):
    if args.output_dir.exists():
        raise ValueError('output_exists_do_not_overwrite_or_replay')
    start, end = (datetime.strptime(x,'%Y-%m-%d %H:%M:%S') for x in (args.start,args.end))
    if end <= start:
        raise ValueError('invalid_exact_window')
    snapshot = load(args.snapshot)
    raw = args.activity_template.read_bytes()
    rate = discount_rate(args.official_rate)
    signup_items = set(args.signup_items.split(',')) if args.signup_items is not None else None
    discount_items = set(args.discount_items.split(',')) if args.discount_items is not None else None
    activity, discounts, issues = build_rows(snapshot,template_rows(raw),rate,args.target,load_bases(args.custom_basis_receipt),signup_items,discount_items)
    result = dict(status='local_input_issues' if issues else 'local_files_ready_not_uploaded',platform_write=False,database_write=False,automatic_retry=False,price_version=snapshot['resolved_price_version_sha256'],official_rate=str(rate),target=args.target,window={'start':args.start,'end':args.end,'timezone':'Asia/Shanghai'},activity_rows=activity,discount_rows=discounts,issues=issues,activity_template_sha256=sha(raw),files=[],note='Activity scope excludes already-successful whole items; new-window ordinary discount scope is independent and includes them. This local writer neither checks external overlapping discounts nor authorizes creating them; 01 must reuse known same-window successes and apply the frozen no-overlap rule. Unknown inputs are reported without generating partial upload files.')
    result['explicit_signup_items'] = sorted(signup_items) if signup_items is not None else None
    result['explicit_discount_items'] = sorted(discount_items) if discount_items is not None else None
    outputs = []
    if not issues:
        if activity:
            outputs.append(('活动报名.xlsx',fill_selected_rows(raw,activity,official_rate=args.official_rate)))
        if discounts:
            discount_raw = args.discount_template.read_bytes()
            result['discount_template_sha256'] = sha(discount_raw)
            outputs.append(('单品立减.xlsx',fill_single_discount_rows(discount_raw,discounts)))
    args.output_dir.mkdir(parents=True,exist_ok=False)
    for name, content in outputs:
        path = args.output_dir/name
        with path.open('xb') as stream:
            stream.write(content)
        result['files'].append(dict(path=str(path.resolve()),sha256=sha(content)))
    with (args.output_dir/'receipt.json').open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--activity-template',type=Path,required=True)
    parser.add_argument('--discount-template',type=Path,required=True)
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

"""Offline candidates only. Never creates upload XLSX, pauses offers or releases a batch."""
import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from campaign_cap_price import ordinary_price

END = '2026-10-07 19:59:59'


def amount(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number != number.quantize(Decimal('.01')):
            raise ValueError('positive_exact_cent_amount_required')
        return number
    except (InvalidOperation,TypeError) as exc:
        raise ValueError('missing_or_invalid_amount') from exc


def candidate(row, mode):
    target = amount(row.get('target'))
    if target < amount(row.get('big_target')):
        raise ValueError('medium_target_below_big_target')
    if mode == 'official_10':
        # P, not ERP daily or G. Existing cap calculator preserves original target +/-2.
        price = amount(row.get('activity_price'))
        cap = amount(row.get('cap'))
        calculated = ordinary_price(price,target,Decimal('.10'),cap)
        return dict(calculated,base_column='P',mode=mode,
                    conditional_on='same window official 10 percent effective; no remaining stacking')
    if mode != 'no_official':
        raise ValueError('unknown_calculation_mode')
    price = amount(row.get('list_price'))
    deduction = price-target
    if deduction < 0:
        raise ValueError('list_price_below_frozen_target')
    return dict(daily=str(price),target=str(target),official_cut='0',deduct=str(deduction),
                final=str(target),delta='0',base_column='G',mode=mode,
                conditional_on='no effective campaign or remaining stacking in replacement window')


def row_audit(row):
    result = dict(item=str(row['item']),sku=str(row['sku']),candidates={},candidate_errors={},
                  selected_candidate=None,blockers=[],upload_ready=False)
    if row.get('mapping_error'):
        result['blockers'].append('erp_mapping_not_verified:'+str(row['mapping_error']))
        return result
    if row.get('custom') is not False:
        result['blockers'].append('custom_or_unclassified_requires_preserved_target_and_fixed_basis')
        return result
    for mode in ('official_10','no_official'):
        try:result['candidates'][mode]=candidate(row,mode)
        except ValueError as exc:result['candidate_errors'][mode]=str(exc)
    mode=row.get('effective_mode','unknown')
    if mode not in ('official_10','no_official'):
        result['blockers'].append('effective_price_and_campaign_mode_unverified')
    elif not row.get('effective_mode_evidence'):
        result['blockers'].append('effective_mode_evidence_missing')
    elif mode not in result['candidates']:
        result['blockers'].append('selected_mode_cannot_be_priced')
    else:
        result['selected_candidate']=result['candidates'][mode]
    # Old K is a readback field, not the pricing base; never fill a missing field with zero.
    if row.get('final') in (None,''):
        result['blockers'].append('current_final_readback_missing')
    if row.get('no_sales_this_campaign'):
        result['notes']=['official admission failure alone does not prove historical discounts inactive']
    return result


def audit(request):
    if (request.get('schema')!='single_discount_replacement_audit_v1'
            or request.get('end')!=END or request.get('target')!='medium'
            or request.get('campaign')!='legacy/itemApply/3172207691'
            or not request.get('user_authorization')
            or request.get('platform_write') is not False):
        raise ValueError('exact_authorized_super_reduce_window_required')
    rows=request.get('rows',[])
    pairs=[(str(r['item']),str(r['sku'])) for r in rows]
    if not rows or len(set(pairs))!=len(pairs) or any(not i.isdigit() or not s.isdigit() for i,s in pairs):
        raise ValueError('exact_unique_audit_scope_required')
    old=request.get('old_offer_members',[])
    old_pairs=set()
    for row in old:
        if not str(row.get('offer_id','')).isdigit() or row.get('end')!=END:
            raise ValueError('old_offer_identity_or_window_outside_authorization')
        old_pairs.add((str(row['item']),str(row['sku'])))
    results=[row_audit(r) for r in rows]
    missing=sorted(old_pairs-set(pairs))
    return dict(schema='single_discount_replacement_audit_result_v1',rows=results,
                counts=dict(rows=len(results),selected_candidates=sum(r['selected_candidate'] is not None for r in results),
                            conditional_official_candidates=sum('official_10' in r['candidates'] for r in results),
                            conditional_no_official_candidates=sum('no_official' in r['candidates'] for r in results)),
                blocker_counts=dict(Counter(b for r in results for b in r['blockers'])),
                old_offer_scope_verified=request.get('old_offer_scope_verified') is True,
                old_offer_member_pairs=len(old_pairs),missing_old_offer_members=missing,
                all_affected_members_accounted_for=bool(old_pairs) and not missing and request.get('old_offer_scope_verified') is True,
                release_blockers=['old_offers_must_remain_unchanged_until_complete_replacement',
                    'exact_pause_receipts_and_remaining_stacking_readback_required',
                    'custom_or_missing_rows_need_explicit_disposition',
                    'this_entry_has_no_upload_release_or_submission_guard'],
                prepared_only=True,not_registered=True,upload_ready=False,platform_write=False,
                xlsx_generated=False,activity_resubmission_allowed=False,
                note='conditional numeric candidates are not approved upload amounts or current platform facts')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--input-sha256',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raw=args.input.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=args.input_sha256:
        raise ValueError('request_hash_changed')
    request=json.loads(raw.decode('utf-8-sig'))
    for ref in request.get('sources',[]):
        if hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()!=ref['sha256']:
            raise ValueError('source_hash_changed:'+ref['path'])
    result=audit(request)
    result.update(input=str(args.input.resolve()),input_sha256=args.input_sha256,sources=request.get('sources',[]))
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(args.output),**result['counts'],upload_ready=False),ensure_ascii=False))


if __name__=='__main__':
    main()

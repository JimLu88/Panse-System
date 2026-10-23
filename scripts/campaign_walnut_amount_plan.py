"""Read-only amount planning from official components; never issues a claim.

The ERP cost sync changes future defaults. This event deliberately preserves
the official enrollment base and uses source List K without a commission uplift.
"""
from decimal import Decimal

OFFER = '150177186781'
START, END = '2026-10-20 20:00:00', '2026-11-13 23:59:59'
WINDOW_TEXT = START + '-' + END


def money(value):
    value = Decimal(str(value))
    if not value.is_finite() or value < 0 or value != value.quantize(Decimal('.01')):
        raise ValueError('invalid_money')
    return value


def calculate(row, current_old):
    """Exact official rows, not inferred page order or the updated ERP daily."""
    lines = row.get('official_lines', [])
    if len(lines) != 3:
        raise ValueError('exact_three_official_components_required')
    item, sku = row['reference_item_id'], row['reference_sku_id']
    by_tool = {}
    for line in lines:
        tool = line['工具名称']
        if tool in by_tool or tool not in ('官方大促', '官方立减', '单品立减'):
            raise ValueError('unknown_or_duplicate_component')
        if (str(line['商品Id']), str(line['skuId'])) != (item, sku):
            raise ValueError('official_identity_mismatch')
        if line['活动时间'] != WINDOW_TEXT or line['优惠层级'] != '单品级':
            raise ValueError('official_window_or_level_mismatch')
        by_tool[tool] = line
    if str(by_tool['单品立减']['活动id']) != OFFER:
        raise ValueError('wrong_existing_offer')
    for key in ('一口价', '标价', '1件预估价(公域)'):
        if len({money(line[key]) for line in lines}) != 1:
            raise ValueError('inconsistent_official_prices')
    original = money(lines[0]['一口价'])
    signup = money(lines[0]['标价'])
    estimate = money(lines[0]['1件预估价(公域)'])
    discount = money(by_tool['单品立减']['优惠金额'])
    official_cut = money(by_tool['官方立减']['优惠金额'])
    if original - money(by_tool['官方大促']['优惠金额']) != signup:
        raise ValueError('signup_component_mismatch')
    if original - sum(money(line['优惠金额']) for line in lines) != estimate:
        raise ValueError('official_components_unbalanced')
    if discount != money(current_old):
        raise ValueError('current_old_amount_changed')
    target = money(row['target_buyer_price'])
    if target <= 0:
        raise ValueError('positive_target_required')
    wanted = money(signup - official_cut - target)
    return dict(item=item, sku=sku, erp_code=row['sku_code'],
                offer_id=OFFER, daily=str(signup), target=str(target),
                final=str(target), old_deduct=str(discount),
                new_deduct=str(wanted), deduct=str(wanted),
                unchanged=wanted == discount, source_cell=row['source_cell'])


def plan(evidence, readback):
    if (readback.get('state') != 'succeeded'
            or readback.get('platform_write_started') is not False):
        raise ValueError('successful_readonly_job_required')
    result = readback['result']
    if (result.get('coverage_verified') is not True or result.get('issues')
            or result.get('platform_write') is not False
            or result.get('price_window') != {'start': START, 'end': END}):
        raise ValueError('readback_coverage_or_window_failed')
    rows, blocked, seen = [], [], set()
    for row in evidence['rows']:
        pair = row['reference_item_id'], row['reference_sku_id']
        if pair in seen and all(pair):
            raise ValueError('duplicate_target_identity')
        seen.add(pair)
        try:
            if not all(pair):
                raise ValueError('missing_current_identity')
            groups = [g for g in result['rows'] if g['item'] == pair[0]]
            if {g['mode'] for g in groups} != {'商品级', 'SKU级'} or len(groups) != 2:
                raise ValueError('both_offer_modes_required')
            overlaps = [o for g in groups for o in g['offers']
                        if o.get('overlaps_requested_window')]
            if len(overlaps) != 1:
                raise ValueError('overlapping_offer_not_unique')
            offer = overlaps[0]
            if (offer['offer_id'] != OFFER or offer.get('mode') != 'SKU级'
                    or offer.get('sku_amounts_verified') is not True
                    or (offer['start'], offer['end']) != (START, END)):
                raise ValueError('exact_verified_offer_required')
            calculated = calculate(row, offer['values'][pair[1]])
            # Existence of an ID in official prices is not current merchant-code proof.
            calculated['write_ready'] = False
            calculated['remaining_gate'] = 'current_merchant_code_identity_and_bound_claim'
            rows.append(calculated)
        except (ValueError, KeyError) as exc:
            blocked.append(dict(erp_code=row['sku_code'], item=pair[0], sku=pair[1], reason=str(exc)))
    return dict(schema='walnut-amount-plan-v1', rows=rows, blocked=blocked,
                platform_write=False, claim_created=False,
                signup_prices_changed=False, erp_prices_changed=False)


if __name__ == '__main__':
    import argparse
    import hashlib
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('--components', type=Path, required=True)
    parser.add_argument('--readback', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.components.read_text('utf-8-sig'))
    if hashlib.sha256(Path(evidence['source']).read_bytes()).hexdigest() != evidence['sha256']:
        raise ValueError('original_official_export_changed')
    output = plan(evidence, json.loads(args.readback.read_text('utf-8-sig')))
    output['sources'] = [{'path': str(p.resolve()), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in (args.components, args.readback)]
    if args.output.exists():
        raise ValueError('output_exists_no_overwrite')
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'rows': len(output['rows']), 'blocked': len(output['blocked']),
                      'write_ready': False, 'output': str(args.output)}, ensure_ascii=False))

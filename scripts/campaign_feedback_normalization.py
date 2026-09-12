"""Join parsed official constraints with the SAME submitted price version.

No additional page scan or price refresh. Missing/ambiguous facts remain manual.
One SKU with several constraints receives one combined decision, not competing
repairs where the last reason silently overwrites the stricter limit.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR

from campaign_continuous_policy import money
from campaign_generate_current_files import official_cut


def normalize_errors(parsed, *, submitted_rows, erp_rows, fixed_bases, actual_discounts, rate, target_mode=None):
    rate = Decimal(str(rate))
    if not rate.is_finite() or not Decimal('0') <= rate < Decimal('1'):
        raise ValueError('invalid_official_rate')
    submitted = {}
    for row in submitted_rows:
        pair = str(row['item']), str(row['sku'])
        if pair in submitted:
            raise ValueError('duplicate_submitted_sku')
        submitted[pair] = row
    erp = defaultdict(list)
    for row in erp_rows:
        erp[row['code']].append(row)
    discounts = defaultdict(list)
    for row in actual_discounts:
        discounts[(str(row['item']), str(row['sku']))].append(row)
    grouped = defaultdict(list)
    for error in parsed['errors']:
        grouped[(error['item'], error['sku'])].append(error)
    normalized = []
    for pair, constraints in grouped.items():
        base = dict(constraints[0], constraints=constraints)
        if all(e['kind'] in ('no_sales','mapping') for e in constraints):
            normalized.append(base)
            continue
        if not pair[1] or any(e['kind'] == 'unknown' for e in constraints):
            normalized.append(dict(base, kind='unknown'))
            continue
        source = submitted.get(pair)
        matches = erp[source['erp_code']] if source else []
        if len(matches) != 1 or type(matches[0].get('custom')) is not bool:
            normalized.append(dict(base, kind='unknown', parse_issue='submitted_erp_mapping_missing_or_ambiguous'))
            continue
        facts = matches[0]
        try:
            daily = money(facts['daily'])
            price = money(source['activity_price'])
            if any(money(e['submitted_price']) != price for e in constraints):
                raise ValueError('feedback_submitted_price_does_not_match_claimed_file')
            base.update(custom=facts['custom'], erp_daily=str(daily), submitted_price=str(price),
                        erp_code=source['erp_code'])
            list_caps = [money(e['official_cap']) for e in constraints if e['kind']=='list_price']
            approved_caps = [money(e['official_cap'])-Decimal('.01') for e in constraints
                             if e['kind']=='approved_price']
            coupon_caps = [money(e['official_cap']) for e in constraints if e['kind']=='coupon_price']
            if facts['custom']:
                basis = fixed_bases.get(pair)
                if not basis or not basis.get('source_sha256') or basis.get('uncertain') is True:
                    raise ValueError('fixed_original_evidence_missing')
                # Conservative maximum in cents; the actual official rounded
                # cut cannot make this exceed the cap. Do not invent coupons.
                feasible = min([price, daily] + list_caps + approved_caps + [
                    (cap/(1-rate)).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
                    for cap in coupon_caps])
                base.update(fixed_original=str(money(basis['original'])),
                            fixed_basis_evidence=basis['source_sha256'],
                            feasible_signup_price=str(feasible), kind='custom_price')
            elif price != daily:
                # classify() corrects our generated price back to daily first.
                base['kind'] = 'signup_not_daily'
            elif approved_caps:
                base['kind']='signup_not_daily'
            else:
                # A proven coupon ceiling below target-2 cannot possibly fit
                # the authorized range, regardless of unexplained stacking.
                # Route to approval without guessing/editing any deduction.
                modes={r.get('target') for r in discounts[pair]}
                mode=target_mode if target_mode is not None else next(iter(modes)) if len(modes)==1 else None
                if mode not in ('big','medium'):raise ValueError('fixed_erp_target_mode_unknown')
                target = money(facts['big_target'] if mode=='big' else facts['medium_target'])
                if coupon_caps and min(coupon_caps)<target-Decimal('2'):
                    base.update(kind='coupon_price',erp_final_target=str(target),
                                feasible_final_price=str(min(coupon_caps)))
                    normalized.append(base)
                    continue
                rows = discounts[pair]
                if len(rows) != 1 or rows[0].get('verified_readback') is not True or not rows[0].get('evidence'):
                    raise ValueError('exact_existing_discount_readback_missing')
                current = Decimal(str(rows[0]['deduct']))
                if not current.is_finite() or current < 0:
                    raise ValueError('invalid_existing_discount')
                target = money(facts['big_target'] if rows[0]['target']=='big' else facts['medium_target'])
                cut = official_cut(daily, rate)
                current_final = daily-cut-current
                observed = [money(e['observed_final']) for e in constraints if e['kind']=='coupon_price']
                needed = max([current] + [daily-cap for cap in list_caps]
                             + [daily-cut-cap for cap in coupon_caps]
                             + [current+money(e['observed_final'])-money(e['official_cap'])
                                for e in constraints if e['kind']=='coupon_price'])
                feasible = daily-cut-needed
                if any(value != current_final for value in observed):
                    # Do not invent the cause of the discrepancy. A small repair
                    # is still within the user's rule only when BOTH the ERP
                    # calculation and every actual official observation, before
                    # and after the exact deduction delta, remain within 2 CNY.
                    bounds=[current_final,feasible,*observed,*[v-(needed-current) for v in observed]]
                    if any(abs(v-target)>Decimal('2') or v<=0 for v in bounds):
                        raise ValueError('unexplained_stacked_price_difference')
                    base.update(official_observed_finals=[str(v) for v in observed],
                                calculated_final_before=str(current_final),
                                bounded_observation_adjustment=True)
                base.update(kind='coupon_price' if coupon_caps else 'list_price',
                            erp_final_target=str(target), feasible_final_price=str(feasible),
                            current_deduct=str(current), proposed_deduct=str(needed),
                            discount_readback_evidence=rows[0]['evidence'])
            normalized.append(base)
        except (KeyError, ValueError, ArithmeticError) as exc:
            normalized.append(dict(base, kind='unknown', parse_issue=str(exc)))
    return normalized

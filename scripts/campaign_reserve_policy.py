"""Offline failure-scope decisions only; never a signup preflight or write authority.

The input is an evidence-backed inventory, not a request to scan the store.
Missing evidence stays unknown. A recommendation never authorizes platform writes.
"""
import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path


def fixed_basis(original, floor):
    """No rounding down and no rebasing from the most recently reduced price."""
    try:
        original, floor = Decimal(str(original)), Decimal(str(floor))
        if not original.is_finite() or not floor.is_finite():
            raise ValueError('nonfinite_fixed_custom_basis')
        if original <= 0 or floor != original * Decimal('.20'):
            raise ValueError('invalid_fixed_custom_basis')
    except InvalidOperation as exc:
        raise ValueError('invalid_fixed_custom_basis') from exc
    return original, floor


def inherit_basis(basis, lineage):
    """Attach an exact, verified old/new mapping without changing its price origin.

    Called only after a saved platform mapping receipt, not when planning a SKU.
    This does NOT certify a new SKU's eligibility or historical-price reset.
    """
    fixed_basis(basis['original'], basis['floor'])
    if not basis.get('source'):
        raise ValueError('missing_original_basis_source')
    required = ('item', 'erp_code', 'source_sku', 'replacement_sku', 'receipt')
    if not all(isinstance(lineage.get(k), str) and lineage[k].strip() for k in required):
        raise ValueError('incomplete_verified_lineage')
    if lineage.get('mapping_verified') is not True or lineage['source_sku'] == lineage['replacement_sku']:
        raise ValueError('invalid_verified_lineage')
    result = deepcopy(basis)
    history = result.setdefault('lineage', [])
    if history:
        previous = history[-1]
        if (previous['item'], previous['erp_code'], previous['replacement_sku']) != (lineage['item'], lineage['erp_code'], lineage['source_sku']):
            raise ValueError('lineage_business_identity_changed')
    if lineage['replacement_sku'] in {r['source_sku'] for r in history}:
        raise ValueError('lineage_cycle')
    history.append(deepcopy(lineage))
    return result


def decide(row):
    """Classify one already-failed SKU; no automatic price change or rotation."""
    if row.get('classification_verified') is not True or row.get('custom') is not True:
        return 'ordinary_or_classification_needs_decision'
    if row.get('failed_current_scope') is not True:
        return 'outside_current_failed_scope'
    if row.get('basis') is None:
        return 'unknown_original_basis'
    basis = row['basis']
    _, floor = fixed_basis(basis['original'], basis['floor'])
    if not basis.get('source'):
        return 'unknown_original_basis'
    # Only a proven feasible signup price (including all applicable constraints)
    # supports direct repair. One error's ceiling alone is not enough.
    candidate = row.get('verified_feasible_signup_price')
    if candidate is not None:
        candidate = Decimal(str(candidate))
        if not candidate.is_finite() or candidate <= 0 or candidate != candidate.quantize(Decimal('.01')):
            raise ValueError('invalid_feasible_signup_price')
        if candidate >= floor:
            return 'direct_price_fix_no_rotation_needed'
    if row.get('rotation_needed_verified') is not True:
        return 'rotation_need_unknown'
    reserves = row.get('reserves', [])
    usable = [r for r in reserves if r.get('mapping_verified') is True
              and r.get('item') == row['item'] and r.get('erp_code') == row['erp_code']
              and r.get('sku') and r['sku'] != row['sku']
              and r.get('enabled') is False and r.get('attributes_match') is True
              and r.get('usable_verified') is True and r.get('evidence')]
    if usable:
        return 'reuse_existing_reserve'
    if row.get('reserve_inventory_complete') is not True or not row.get('reserve_inventory_evidence'):
        return 'unknown_reserve_gap'
    # Even a complete list does not make an incompletely checked reserve absent.
    if any(r.get('unusable_verified') is not True for r in reserves):
        return 'unknown_reserve_gap'
    return 'create_reserve_needed'


def inventory(rows):
    seen, results = set(), []
    for row in rows:
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in ('item', 'sku', 'erp_code')):
            raise ValueError('missing_exact_identity')
        pair = row['item'], row['sku']
        if pair in seen:
            raise ValueError('duplicate_failed_identity')
        seen.add(pair)
        results.append(dict(item=row['item'], sku=row['sku'], erp_code=row['erp_code'], action=decide(row)))
    return dict(mode='offline_recommendation_not_write_authority', platform_write=False,
                automatic_rotation=False, affected_skus=len(results),
                affected_items=len({r['item'] for r in results}),
                counts=dict(Counter(r['action'] for r in results)), rows=results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory_json', type=Path)
    args = parser.parse_args()
    print(json.dumps(inventory(json.loads(args.inventory_json.read_text(encoding='utf-8-sig'))['rows']), ensure_ascii=False, indent=2))

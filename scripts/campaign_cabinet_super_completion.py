"""2026-09-16 exact user-authorized addition, not a successful-item replay.

Only the two new cabinet SKUs, existing marketing/offer, and September 28-30.
The frozen general success/unknown guards remain unchanged.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from campaign_generate_current_files import official_cut

AUTH = 'user-20260916-finish-cabinet-two-new-super-skus'
ITEM = '793052650673'
MARKETING = '10030564831691'
OFFER = '145812384556'
WINDOW = dict(start='2026-09-28 00:00:00', end='2026-09-30 23:59:59')
IDENTITY = dict(campaign_id='legacy', phase_id='itemApply', sign_record_id='3172207691',
    title='超级立减长期活动', phase_title='商品提交', shop_name='畔色木作',
    start='2025-06-21 00:00:00', end='2028-07-31 23:59:59', rate_label='10%')
TARGETS = {
    '6134178749284': dict(code='PPS2435001041011', daily='6285.00', target='3920.30', floor='3806.12'),
    '6134178749285': dict(code='PPS2435001041012', daily='6667.50', target='4151.53', floor='4030.61'),
}
PRESERVED = ('5933484871217', '6150018654216', '6222326971493')
SKUS = tuple(TARGETS) + PRESERVED
SOURCE = Path('D:/AI/畔色ERP系统/outputs/campaign-continuous/runs/0c3b394ac44b9d8e3a645c481da86d1ba6adc81069414f5a04788a8d15a8e349/segments/09227bb39b981f1fe0907d79012e2bd0c460bf6d59510ef7ab87a8aa9f159f44/resolved-snapshot.json')
SOURCE_SHA = '3aed1a06b3c35a5880abc800e1883bc7d76bc877a802a2101746c8c2e8424e62'


def payload():
    return deepcopy(dict(identity=IDENTITY, authorization=AUTH, item=ITEM,
        marketing_id=MARKETING, offer_id=OFFER, price_window=WINDOW, targets=TARGETS))


def validate(value):
    if value != payload():
        raise ValueError('cabinet_completion_exact_authorization_required')


def deductions(snapshot=None):
    if snapshot is None:
        raw = SOURCE.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
            raise ValueError('cabinet_completion_price_source_changed')
        snapshot = json.loads(raw)
    result = {}
    for sku, spec in TARGETS.items():
        rows = [r for r in snapshot['all_erp_rows'] if r['code'] == spec['code']]
        if len(rows) != 1:
            raise ValueError('cabinet_completion_erp_mapping_not_unique')
        row = rows[0]
        if (row['item'] != ITEM or sku not in row['alt'] or row['custom'] is not False
                or any(Decimal(str(row[k])) != Decimal(spec[v]) for k, v in
                       [('daily','daily'), ('medium_target','target'), ('big_target','floor')])):
            raise ValueError('cabinet_completion_price_or_mapping_changed')
        daily, target, floor = [Decimal(spec[k]) for k in ('daily','target','floor')]
        cut = official_cut(daily, Decimal('.10'))
        deduct = daily-cut-target
        if not floor <= target <= daily or deduct < 0 or deduct != deduct.quantize(Decimal('.01')):
            raise ValueError('cabinet_completion_price_assertion_failed')
        result[sku] = format(deduct, '.2f')
    return result


def editor_prices(inventory):
    result = {}
    for row in inventory.get('rows', []):
        sku = row['sku_id']; fields = row.get('inputs', [])
        if sku in result or len(fields) != 1 or fields[0].get('type') not in ('text','number'):
            raise ValueError('cabinet_completion_editor_shape_changed')
        text = fields[0]['value'].strip()
        if not text and sku in TARGETS:
            result[sku] = None
        else:
            value = Decimal(text)
            if not value.is_finite() or value <= 0:
                raise ValueError('cabinet_completion_invalid_existing_price')
            result[sku] = format(value, '.2f')
    if set(result) != set(SKUS):
        raise ValueError('cabinet_completion_active_sku_scope_changed')
    for sku, spec in TARGETS.items():
        if result[sku] not in (None, spec['daily']):
            raise ValueError('cabinet_completion_existing_target_price_conflict')
    return result


def missing_discounts(observed, wanted):
    if (observed.get('item') != ITEM or observed.get('requested_scope_verified') is not True
            or any(observed.get('window', {}).get(k) != v for k, v in dict(WINDOW, offer_id=OFFER).items())):
        raise ValueError('cabinet_completion_discount_scope_or_window_changed')
    values = observed.get('values', {}); missing = observed.get('not_enrolled', [])
    if (set(values) & set(missing) or set(values) | set(missing) != set(SKUS)
            or len(missing) != len(set(missing))):
        raise ValueError('cabinet_completion_discount_membership_incomplete')
    for sku, amount in wanted.items():
        if sku in values and Decimal(values[sku]) != Decimal(amount):
            raise ValueError('cabinet_completion_saved_discount_conflict')
    return {s: wanted[s] for s in wanted if s in missing}


def verify_preserved(before, after, target_ids):
    if set(before) != set(after) or any(before[s] != after[s] for s in before if s not in target_ids):
        raise ValueError('cabinet_completion_non_target_changed')


def final_acceptance(rows, expected):
    from campaign_final_audit import ACCEPTED
    selected = []
    for sku in SKUS:
        found = [r for r in rows if r['item'] == ITEM and r['sku'] == sku
                 and r['state'] not in ('撤销报名','已撤销','已取消')]
        if len(found) != 1:
            raise ValueError('cabinet_completion_final_sku_not_unique')
        row = found[0]
        if (row['marketing_id'] != MARKETING or row['state'] not in ACCEPTED
                or Decimal(row['activity_price']) != Decimal(expected[sku])):
            raise ValueError('cabinet_completion_official_price_or_status_not_verified')
        selected.append(row)
    return dict(all_registered=True, registered_skus=5, added_target_skus=list(TARGETS), official_rows=selected)

"""Operator-owned finite cost sync. No platform transport or order writes.

Preview binds complete old rows/history/settings; apply recomputes that preview
under a write exclusion lock and requires its exact hash. Caller owns commit.
Same-day historical boundaries are rejected, not silently ignored.
"""
from copy import deepcopy
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from sqlalchemy import select, text
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.pricing_version import PricingSkuVersion
from app.models.settings import SystemSetting
from app.services.pricing_calc_service import recompute, recompute_promo, get_promo_params
from app.services.pricing_version_service import record_dated_change, _last_end

COMPONENTS = {'wood_cost', 'packaging_cost', 'external_parts_cost', 'logistics_cost', 'install_cost'}
BASES = {'base_list', 'base_small', 'base_mid', 'base_big'}
PARTS = {c.name for c in PricingSkuCosts.__table__.columns if c.name not in {
    'id', 'sku_code', 'created_at', 'updated_at', 'other_desc', 'parts_remark', 'bom_synced_at', 'stale_reason'}}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode()).hexdigest()


def snapshot(obj):
    if obj is None:
        return None
    return {c.name: (str(v) if isinstance(v, Decimal) else v.isoformat() if hasattr(v, 'isoformat') else v)
            for c in obj.__table__.columns for v in [getattr(obj, c.name)]}


def number(value, *, base=False):
    try:
        v = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('missing_or_invalid_numeric_source') from None
    if not v.is_finite() or v < 0 or (base and not 0 < v <= 1):
        raise ValueError('numeric_source_out_of_range')
    if v != v.quantize(Decimal('.0001') if base else Decimal('.01')):
        raise ValueError('source_precision_exceeds_database')
    return v


def validate(manifest):
    if manifest.get('schema') != 'pricing-scoped-sync-v1':
        raise ValueError('unsupported_manifest')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', str(manifest.get('request_id', ''))):
        raise ValueError('request_id_required')
    if not re.fullmatch(r'[0-9a-f]{64}', str(manifest.get('source_sha256', ''))):
        raise ValueError('source_hash_required')
    if not manifest.get('authorization_ref') or manifest.get('margin_policy') != 'source_cost_plus_keep_erp_mid_floor':
        raise ValueError('explicit_authority_and_mid_policy_required')
    rows = manifest.get('rows')
    if not isinstance(rows, list) or not 0 < len(rows) <= 200:
        raise ValueError('finite_rows_required')
    codes = [r['sku_code'] for r in rows]
    if len(set(codes)) != len(codes) or set(codes) != set(manifest.get('authorized_sku_codes', [])):
        raise ValueError('duplicate_or_unapproved_scope')
    date.fromisoformat(manifest['effective_from'])
    for r in rows:
        if set(r) != {'sku_code', 'product_code', 'sku', 'components', 'bases', 'parts', 'source_prices'}:
            raise ValueError('unexpected_row_fields')
        if set(r['components']) != COMPONENTS or set(r['bases']) != BASES or set(r['parts']) != PARTS:
            raise ValueError('complete_cost_and_formula_fields_required')
        if set(r['source_prices']) != {'list_price', 'daily_price', 'small_promo', 'mid_promo', 'big_promo'}:
            raise ValueError('complete_source_prices_required')
        for group in ('components', 'parts', 'source_prices', 'bases'):
            for value in r[group].values():
                number(value, base=group == 'bases')
        if sum(map(number, r['parts'].values())) != number(r['components']['external_parts_cost']):
            raise ValueError('external_parts_sum_conflict')


def preview(db, manifest):
    validate(manifest)
    params = get_promo_params(db)
    result = {'schema': 'pricing-scoped-sync-preview-v1', 'manifest_sha256': digest(manifest),
              'parameters': params, 'rows': [], 'platform_write': False}
    effective = date.fromisoformat(manifest['effective_from'])
    for r in sorted(manifest['rows'], key=lambda r: r['sku_code']):
        sku = db.execute(select(PricingSku).where(PricingSku.sku_code == r['sku_code'])).scalar_one_or_none()
        if sku is None or (sku.product_code, sku.sku) != (r['product_code'], r['sku']):
            raise ValueError('exact_sku_identity_mismatch:' + r['sku_code'])
        if sku.is_custom_placeholder:
            raise ValueError('custom_placeholder_not_cost_syncable')
        old_end = _last_end(db, sku.sku_code)
        if old_end is not None and effective <= old_end:
            raise ValueError('same_day_or_backdated_history_requires_review')
        costs = db.execute(select(PricingSkuCosts).where(PricingSkuCosts.sku_code == sku.sku_code)).scalar_one_or_none()
        promo = db.execute(select(PricingSkuPromo).where(PricingSkuPromo.sku_code == sku.sku_code)).scalar_one_or_none()
        if costs is None or promo is None:
            raise ValueError('existing_cost_and_promo_rows_required')
        histories = db.execute(select(PricingSkuVersion).where(PricingSkuVersion.sku_code == sku.sku_code)
                               .order_by(PricingSkuVersion.id)).scalars().all()
        new = PricingSku(**{c.name: deepcopy(getattr(sku, c.name)) for c in sku.__table__.columns})
        new_promo = PricingSkuPromo(**{c.name: deepcopy(getattr(promo, c.name)) for c in promo.__table__.columns})
        new_costs = PricingSkuCosts(**{c.name: deepcopy(getattr(costs, c.name)) for c in costs.__table__.columns})
        for k, v in {**r['components'], **r['bases']}.items():
            setattr(new, k, number(v, base=k in BASES))
        for k, v in r['parts'].items():
            setattr(new_costs, k, number(v))
        new.factory_cost_override = False
        recompute(new, params)
        recompute_promo(new_promo, new, params)
        for k in ('list_price', 'daily_price', 'small_promo', 'big_promo'):
            if getattr(new, k) != number(r['source_prices'][k]):
                raise ValueError('source_formula_result_mismatch:' + r['sku_code'] + ':' + k)
        result['rows'].append({'sku_code': sku.sku_code,
            'before': {'sku': snapshot(sku), 'costs': snapshot(costs), 'promo': snapshot(promo),
                       'history': [snapshot(h) for h in histories]},
            'after': {'sku': snapshot(new), 'costs': snapshot(new_costs), 'promo': snapshot(new_promo)},
            'source_mid': str(number(r['source_prices']['mid_promo'])),
            'erp_mid': str(new.mid_promo),
            'event_target_not_erp_buyer': str(number(r['source_prices']['big_promo']))})
    # JSON normalization makes saved previews identical to in-memory hashes.
    return json.loads(json.dumps(result, default=str, ensure_ascii=False))


def apply(db, manifest, expected_preview_sha256):
    """Requires fresh session. One atomic transaction; caller MUST commit/rollback."""
    validate(manifest)
    if manifest['effective_from'] != datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat():
        raise ValueError('current_business_day_required')
    if db.in_transaction():
        raise ValueError('fresh_transaction_required')
    dialect = db.get_bind().dialect.name
    if dialect == 'postgresql':
        db.execute(text('SET LOCAL lock_timeout = \'5s\''))
        db.execute(text('LOCK TABLE pricing_sku, pricing_sku_costs, pricing_sku_promo, '
                        'pricing_sku_version, system_settings IN SHARE ROW EXCLUSIVE MODE'))
    elif dialect == 'sqlite':
        db.execute(text('BEGIN IMMEDIATE'))
    else:
        raise ValueError('unsupported_atomic_lock_dialect')
    key = 'pricing_scoped_sync:' + manifest['request_id']
    done = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if done is not None:
        receipt = json.loads(done.value_plain)
        if receipt['manifest_sha256'] != digest(manifest) or receipt['preview_sha256'] != expected_preview_sha256:
            raise ValueError('request_id_already_bound')
        return dict(receipt, replayed=False, already_applied=True)
    plan = preview(db, manifest)
    if digest(plan) != expected_preview_sha256:
        raise ValueError('old_values_history_or_parameters_changed')
    effective = date.fromisoformat(manifest['effective_from'])
    for row in plan['rows']:
        sku = db.get(PricingSku, row['before']['sku']['id'])
        history = record_dated_change(db, sku, effective, actor='scoped-cost-sync',
            note=manifest['request_id'], preserve_all=True)
        history_data = json.loads(history.snapshot)
        history_data['scoped_complete_before'] = {k: v for k, v in row['before'].items() if k != 'history'}
        history_data['source_sha256'] = manifest['source_sha256']
        history.snapshot = json.dumps(history_data, ensure_ascii=False)
        for name, model in (('sku', PricingSku), ('costs', PricingSkuCosts), ('promo', PricingSkuPromo)):
            obj = db.get(model, row['before'][name]['id'])
            for col in model.__table__.columns:
                k = col.name
                new = row['after'][name][k]
                if new != row['before'][name][k]:
                    # Only computed and allowed numeric fields change.
                    setattr(obj, k, new if isinstance(new, bool) or new is None else Decimal(new))
    receipt = {'schema': 'pricing-scoped-sync-receipt-v1', 'manifest_sha256': digest(manifest),
               'preview_sha256': expected_preview_sha256, 'source_sha256': manifest['source_sha256'],
               'rows': plan['rows'], 'platform_write': False, 'applied_in_transaction': True}
    db.add(SystemSetting(key=key, value_plain=json.dumps(receipt, ensure_ascii=False),
                         is_secret=False, description='Finite cost sync, full before/after and retained history'))
    db.flush()
    return receipt

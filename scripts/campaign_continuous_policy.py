"""Pure rules for the user's continuous Web-Agent flow. No browser preflight.

Inputs are normalized by the official-report/product-export adapters. Unknown
data never becomes a price, delisting, successful submission or rotation order.
Existing in-flight v1 bundles keep their original rules and receipts.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

CONTRACT = Path(__file__).resolve().parents[1] / 'docs/campaign-continuous-flow-20260911.json'
ENTRY = 'https://myseller.taobao.com/home.htm/starb/tmc-next/sale/seller/homepage.htm'
RULE_SHA = '500d344dc12f904b0166ce0f3bc9cb6a2b867b2702c302be4ac753390b0a0397'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def load_rules():
    rules = json.loads(CONTRACT.read_text(encoding='utf-8'))
    if fingerprint(rules) != RULE_SHA:
        raise ValueError('continuous_rules_changed_require_direct_user_instruction')
    return rules


def money(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing_money')
    d = Decimal(str(value))
    if not d.is_finite() or d <= 0:
        raise ValueError('invalid_money')
    return d


def activity_identity(page, *, expected_shop, observed_links):
    """Only select an actual anchor observed at the user's official entry."""
    if page.get('entry') != ENTRY or page.get('url') not in observed_links:
        raise ValueError('activity_link_not_observed_at_official_entry')
    u = urlsplit(page['url'])
    if (u.scheme != 'https' or u.username or u.password or u.port not in (None, 443)
            or not u.hostname or not u.hostname.endswith('.taobao.com')):
        raise ValueError('activity_not_official_taobao_url')
    if not expected_shop or page.get('shop_id') != expected_shop:
        raise ValueError('activity_shop_mismatch')
    fields = ('campaign_id', 'phase_id', 'title', 'start', 'end', 'official_rate')
    if any(not page.get(k) for k in fields) or not page.get('page_evidence'):
        raise ValueError('activity_identity_incomplete')
    start = datetime.fromisoformat(page['start'])
    end = datetime.fromisoformat(page['end'])
    if start >= end:
        raise ValueError('activity_window_invalid')
    # Shop, exact IDs and window, not changing titles/URLs, define newness.
    return fingerprint({k: page[k] for k in ('shop_id', 'campaign_id', 'phase_id', 'start', 'end')})


def discovery_due(last_check, now, known_deadline=None):
    """72-hour interval, optionally earlier at an already-known checkpoint."""
    return (last_check is None or now >= last_check + timedelta(hours=72)
            or (known_deadline is not None and last_check < known_deadline <= now))


def signup_scope(erp_sellable, platform_rows, *, complete, observed_item_count):
    if complete is not True:
        raise ValueError('official_product_scope_incomplete')
    by_item = {}
    for row in platform_rows:
        item, status = str(row.get('item') or ''), row.get('on_sale')
        if not item or status not in (True, False) or type(status) is not bool:
            raise ValueError('official_listing_state_unknown')
        if item in by_item and by_item[item] != status:
            raise ValueError('conflicting_official_listing_state')
        by_item[item] = status
    if len(by_item) != observed_item_count:
        raise ValueError('official_product_count_mismatch')
    return sorted(set(map(str, erp_sellable)) & {k for k, v in by_item.items() if v})


def classify(error):
    """Return a decision, never perform an edit. Needs exact official failure."""
    base = {'item': str(error.get('item') or ''), 'sku': str(error.get('sku') or ''),
            'reason': error.get('message', ''), 'action': 'manual', 'repair': None}
    if (not base['item'] or error.get('terminal') != 'failed'
            or not error.get('batch') or not error.get('official_evidence')):
        return dict(base, reason='official_failed_scope_not_proven')
    kind = error.get('kind')
    if kind=='unknown' and error.get('parse_issue'):
        return dict(base,reason=error['parse_issue'])
    if kind == 'no_sales':
        return dict(base, action='not_eligible_this_campaign')
    if not base['sku']:
        return dict(base, reason='official_failed_sku_not_proven')
    if kind == 'mapping':
        if (error.get('full_official_export_verified') is True
                and error.get('official_invalid_or_disabled') is True
                and error.get('remove_from_signup') is True and error.get('mapping_scope_evidence')):
            return dict(base, action='repair', repair={'kind':'exclude_ineligible_sku',
                'scope_evidence':error['mapping_scope_evidence']})
        matches = error.get('verified_mapping_candidates')
        if (error.get('full_official_export_verified') is True
                and isinstance(matches, list) and len(matches) == 1
                and all(matches[0].get(k) for k in ('item', 'sku', 'erp_code'))
                and str(matches[0]['item']) == base['item']):
            return dict(base, action='repair', repair={'kind': 'mapping', 'value': matches[0]})
        return dict(base, reason='mapping_missing_or_ambiguous')
    if type(error.get('custom')) is not bool:
        return dict(base, reason='sku_classification_unknown')
    try:
        daily = money(error.get('erp_daily'))
        submitted = money(error.get('submitted_price'))
        if error.get('custom') is not True and submitted != daily:
            # Correct our generated value. Never change ERP daily to match a cap.
            return dict(base, action='repair', repair={'kind': 'file_price', 'price': str(daily)})
        if kind == 'signup_not_daily':
            return dict(base, reason='platform_requires_non_daily_signup_price')
        if error.get('custom') is True:
            if kind not in ('custom_price', 'coupon_price', 'list_price', 'approved_price'):
                return base
            if not error.get('fixed_basis_evidence'):
                return dict(base, reason='fixed_basis_unknown_not_rotation_proof')
            floor = money(error.get('fixed_original')) * Decimal('.20')
            minimum = floor.quantize(Decimal('.01'), rounding=ROUND_CEILING)
            proposed = money(error.get('feasible_signup_price'))
            if proposed != proposed.quantize(Decimal('.01')) or proposed > daily:
                return dict(base, reason='custom_feasible_price_invalid')
            if proposed < minimum:
                return dict(base, action='rotation_approval', reason='below_fixed_twenty_percent')
            return dict(base, action='repair', repair={'kind': 'custom_price', 'price': str(proposed),
                                                      'exact_floor': str(floor)})
        if kind in ('coupon_price', 'list_price'):
            target = money(error.get('erp_final_target'))
            final = money(error.get('feasible_final_price'))
            if abs(final - target) > Decimal('2.00'):
                return dict(base, action='rotation_approval', reason='final_delta_over_two_yuan')
            return dict(base, action='repair', repair={'kind': 'ordinary_discount',
                'final': str(final), 'target': str(target), 'signup_price': str(daily)})
    except (ValueError, ArithmeticError):
        return dict(base, reason='price_evidence_incomplete')
    return base


def classify_items(errors):
    """Keep a product's full SKU set together; a manual SKU holds that product only."""
    groups = {}
    for error in errors:
        decision = classify(error)
        groups.setdefault(decision['item'], []).append(decision)
    repair, exceptions = {}, {}
    for item, decisions in groups.items():
        if all(d['action'] == 'repair' for d in decisions):
            repair[item] = decisions
        else:
            exceptions[item] = decisions
    return repair, exceptions

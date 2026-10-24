"""Explicit, versioned SKU price calculation. Never reads Panse settings or data."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING

VERSION = 'fuge-explicit-price-v2'
CENT = Decimal('0.01')

def number(value, name):
    if value is None or isinstance(value, bool):
        raise ValueError(name + ': missing')
    n = Decimal(str(value))
    if not n.is_finite() or n < 0 or n > Decimal('100000000'):
        raise ValueError(name + ': invalid')
    return n

def calculate(p):
    if p.get('confirmed') is not True:
        raise ValueError('parameters_not_confirmed')
    for key in ('source_item_id','source_sku_id','specification','source_url','price_basis','conditions','discount_components','observed_at','rule_version'):
        if not p.get(key):
            raise ValueError('missing_' + key)
    if p['rule_version'] != VERSION or p['price_basis'] != 'platform_pre_coupon':
        raise ValueError('unsupported_price_basis_or_version')
    observed = datetime.fromisoformat(p['observed_at'].replace('Z','+00:00'))
    now = datetime.now(timezone.utc)
    if observed.tzinfo is None or observed > now + timedelta(minutes=5) or now-observed > timedelta(hours=24):
        raise ValueError('source_not_current')
    if p.get('shop_and_personal_coupons_excluded') is not True:
        raise ValueError('coupon_exclusion_not_confirmed')
    competitor = number(p.get('platform_pre_coupon_price'), 'platform_pre_coupon_price')
    listing = number(p.get('list_price'), 'list_price')
    shop_rate = number(p.get('single_item_rate'), 'single_item_rate')
    official_rate = number(p.get('official_rate'), 'official_rate')
    if competitor <= 0 or listing <= 0 or not 0 < shop_rate <= 1 or not 0 <= official_rate < 1:
        raise ValueError('invalid_price_or_rate')
    # 0.70 is the explicitly requested competitor target, not a cost/fee factor.
    target = (competitor * Decimal('0.70')).quantize(CENT, rounding=ROUND_HALF_UP)
    daily = (listing * shop_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    exact = daily * official_rate
    official = exact.quantize(CENT, rounding=ROUND_HALF_UP) if daily < 100 else exact.to_integral_value(rounding=ROUND_CEILING)
    deduction = (daily-official-target).quantize(CENT, rounding=ROUND_HALF_UP)
    if deduction < 0:
        raise ValueError('target_above_activity_base_do_not_clamp')
    return {'version':VERSION,'calculated_at':now.isoformat(),'target_final':str(target),
            'list_price':str(listing),'single_item_rate':str(shop_rate),'signup_price':str(daily),
            'official_rate':str(official_rate),'official_deduction':str(official),
            'single_deduction':str(deduction),'verified_final':str(daily-official-deduction),
            'formula':'daily=round(list*single_item_rate,2); O=ceil(daily*official_rate) [daily<100: cents]; S=daily-O-target',
            'source':p,'platform_verified':False,'approval':'calculation_only'}

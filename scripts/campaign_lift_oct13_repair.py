"""Original-marketing lift repair, derived from pinned official/ERP facts.

Keeps ERP/daily prices and custom SKUs unchanged. Only bounded ordinary
deductions only; all activity prices and four custom/accessory rows are preserved.
"""
from decimal import Decimal
from pathlib import Path
from campaign_entry_authority import file_sha, load
from campaign_generate_current_files import official_cut
from campaign_cabinet_super_completion import IDENTITY, verify_preserved

AUTH = 'user-20261013-fresh-export-lift-inplace-repair'
ITEM, MARKETING, OFFER = '793202812082', '10030968095179', '150586803468'
WINDOW = dict(start='2026-10-13 00:00:00', end='2026-10-16 23:59:59')
SOURCE = Path('D:/AI/畔色ERP系统/outputs/campaign-super-20261013-repair-20261012/fresh-lift-source-20261013.json')
SOURCE_SHA = 'a7ce29cf52fe3b29912c9dc290d3e3ced90a3bf64473782bd6b31fad8a1ed003'


def source():
    if file_sha(SOURCE) != SOURCE_SHA:raise ValueError('lift_source_changed')
    doc = load(SOURCE)
    for path, sha in [('source','source_sha256'),('erp_source','erp_sha256'),('original_offer_source','original_offer_sha256')]:
        if file_sha(doc[path]) != doc[sha]:raise ValueError('lift_original_fact_changed')
    batch=doc['original_offer_readback']
    offers=[o for row in batch['rows'] if row['mode']=='SKU级'
            for o in row['offers'] if o['offer_id']==OFFER]
    if len(offers)!=1 or offers[0].get('requested_scope_verified') is not True:
        raise ValueError('lift_original_offer_readback_incomplete')
    # Product-level coverage is not proven here. The executor must recheck
    # both modes before amendment; this source never proves no overlap.
    doc['original_offer_readback']=offers[0]['sku_readback']
    return doc


def derive(doc):
    specs = {}; expected = {}; wanted = {}; old = doc['original_offer_readback']['values']
    for row in doc['rows']:
        o=row['official']; sku=o['SKUID']; matches=row['erp_matches']
        if o['商品ID']!=ITEM or o['营销ID']!=MARKETING or sku in expected or len(matches)!=1:
            raise ValueError('lift_exact_identity_required')
        e=matches[0]; expected[sku]=o['活动价'] or None
        if e['custom'] is True:continue
        if e['custom'] is not False or e['item']!=ITEM or sku not in [e['sku'],*e.get('alt',[])]:
            raise ValueError('lift_ordinary_mapping_required')
        daily=Decimal(e['daily']); goal=Decimal(e['medium_target']); deduction=Decimal(old[sku])
        if expected[sku] is not None and Decimal(expected[sku])!=daily:
            raise ValueError('lift_daily_price_changed')
        observed=Decimal(o['活动普惠券后价']) if o['活动普惠券后价'] else goal
        cap=Decimal(o['最低普惠券后价要求']) if o['最低普惠券后价要求'] else goal
        delta=max(Decimal(0),observed-cap)
        calculated=daily-official_cut(daily,Decimal('.1'))-deduction
        if any(not v.is_finite() or v<=0 or abs(v-goal)>2 for v in
               (observed,calculated,observed-delta,calculated-delta)):
            raise ValueError('lift_cumulative_target_tolerance_exceeded')
        wanted[sku]=format(deduction+delta,'.2f')
        specs[sku]=dict(code=e['code'],daily=format(daily,'.2f'),target=format(goal,'.2f'))
    if len(expected)!=12 or len(specs)!=8:raise ValueError('lift_full_scope_changed')
    return specs, expected, wanted


TARGETS, INITIAL, _ = derive(source())
SKUS = tuple(INITIAL)


def payload():
    return dict(identity=IDENTITY,authorization=AUTH,item=ITEM,marketing_id=MARKETING,
                offer_id=OFFER,price_window=WINDOW,source_sha256=SOURCE_SHA)


def validate(value):
    if value!=payload():raise ValueError('lift_exact_repair_request_required')


def deductions():return derive(source())[2]


def editor_prices(observed):
    values={}
    for row in observed['rows']:
        sku=row.get('sku_id'); fields=row.get('inputs',[])
        if sku not in SKUS:raise ValueError('lift_editor_extra_sku')
        if sku in values or len(fields)!=1:raise ValueError('lift_editor_scope_changed')
        value=fields[0]['value'].strip()
        values[sku]=format(Decimal(value),'.2f') if value else None
    if set(values)!=set(SKUS):raise ValueError('lift_editor_incomplete')
    for sku,v in values.items():
        allowed={INITIAL[sku]}
        if sku in TARGETS:allowed.add(TARGETS[sku]['daily'])
        if v not in allowed:raise ValueError('lift_existing_price_drift')
    return values


def adjustment(observed,wanted):
    if (observed.get('item')!=ITEM or observed.get('requested_scope_verified') is not True
        or any(observed.get('window',{}).get(k)!=v for k,v in dict(WINDOW,offer_id=OFFER).items())):
        raise ValueError('lift_discount_identity_changed')
    values=observed.get('values',{}); missing=observed.get('not_enrolled',[])
    if set(values)&set(missing) or set(values)|set(missing)!=set(SKUS):
        raise ValueError('lift_discount_membership_incomplete')
    original=source()['original_offer_readback']['values']; changes={}
    for sku,new in wanted.items():
        if sku not in values:raise ValueError('lift_previously_saved_discount_missing')
        before=Decimal(values[sku])
        if before not in (Decimal(original[sku]),Decimal(new)):
            raise ValueError('lift_current_deduction_drift')
        if before!=Decimal(new):changes[sku]=dict(old=values[sku],new=new)
    return changes


def missing_discounts(observed,wanted):
    if adjustment(observed,wanted):raise ValueError('lift_discount_amend_not_verified')
    return {}


def final_acceptance(rows,expected):
    from campaign_final_audit import ACCEPTED
    selected=[]
    for sku in SKUS:
        found=[r for r in rows if r['item']==ITEM and r['sku']==sku and r['state'] not in ('撤销报名','已撤销','已取消')]
        if len(found)!=1:raise ValueError('lift_final_scope_not_unique')
        r=found[0]
        if r['marketing_id']!=MARKETING or r['state'] not in ACCEPTED or Decimal(r['activity_price'])!=Decimal(expected[sku]):
            raise ValueError('lift_final_status_or_price_unverified')
        selected.append(r)
    return dict(all_registered=True,registered_skus=len(selected),official_rows=selected)

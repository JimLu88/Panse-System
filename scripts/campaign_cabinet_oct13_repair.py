"""Original-marketing cabinet repair, derived from pinned official/ERP facts.

Keeps ERP/daily prices and custom SKUs unchanged. Only bounded ordinary
deductions and the existing blank metal-leg activity price may change.
"""
from decimal import Decimal
from pathlib import Path
from campaign_entry_authority import file_sha, load
from campaign_generate_current_files import official_cut
from campaign_cabinet_super_completion import IDENTITY, verify_preserved

AUTH = 'user-20261013-fresh-export-cabinet-inplace-repair'
ITEM, MARKETING, OFFER = '793084818113', '10030569662923', '150590304983'
WINDOW = dict(start='2026-10-13 00:00:00', end='2026-10-16 23:59:59')
SOURCE = Path('D:/AI/畔色ERP系统/outputs/campaign-super-20261013-repair-20261012/fresh-cabinet-source-20261013.json')
SOURCE_SHA = '9a253a021303917d35991a6f90449eb2939dc3cbfa65847837b6951d4fb0d75e'


def source():
    if file_sha(SOURCE) != SOURCE_SHA:raise ValueError('cabinet_source_changed')
    doc = load(SOURCE)
    for path, sha in [('source','source_sha256'),('erp_source','erp_sha256'),('original_offer_source','original_offer_sha256')]:
        if file_sha(doc[path]) != doc[sha]:raise ValueError('cabinet_original_fact_changed')
    return doc


def derive(doc):
    specs = {}; expected = {}; wanted = {}; old = doc['original_offer_readback']['values']
    for row in doc['rows']:
        o=row['official']; sku=o['SKUID']; matches=row['erp_matches']
        if o['商品ID']!=ITEM or o['营销ID']!=MARKETING or sku in expected or len(matches)!=1:
            raise ValueError('cabinet_exact_identity_required')
        e=matches[0]; expected[sku]=o['活动价'] or None
        if e['custom'] is True:continue
        if e['custom'] is not False or e['item']!=ITEM or sku not in [e['sku'],*e.get('alt',[])]:
            raise ValueError('cabinet_ordinary_mapping_required')
        daily=Decimal(e['daily']); goal=Decimal(e['medium_target']); deduction=Decimal(old[sku])
        if expected[sku] is not None and Decimal(expected[sku])!=daily:
            raise ValueError('cabinet_daily_price_changed')
        observed=Decimal(o['活动普惠券后价']) if o['活动普惠券后价'] else goal
        cap=Decimal(o['最低普惠券后价要求']) if o['最低普惠券后价要求'] else goal
        delta=max(Decimal(0),observed-cap)
        calculated=daily-official_cut(daily,Decimal('.1'))-deduction
        if any(not v.is_finite() or v<=0 or abs(v-goal)>2 for v in
               (observed,calculated,observed-delta,calculated-delta)):
            raise ValueError('cabinet_cumulative_target_tolerance_exceeded')
        wanted[sku]=format(deduction+delta,'.2f')
        specs[sku]=dict(code=e['code'],daily=format(daily,'.2f'),target=format(goal,'.2f'))
    if len(expected)!=18 or len(specs)!=16:raise ValueError('cabinet_full_scope_changed')
    return specs, expected, wanted


TARGETS, INITIAL, _ = derive(source())
SKUS = tuple(INITIAL)


def payload():
    return dict(identity=IDENTITY,authorization=AUTH,item=ITEM,marketing_id=MARKETING,
                offer_id=OFFER,price_window=WINDOW,source_sha256=SOURCE_SHA)


def validate(value):
    if value!=payload():raise ValueError('cabinet_exact_repair_request_required')


def deductions():return derive(source())[2]


def editor_prices(observed):
    values={}
    for row in observed['rows']:
        sku=row.get('sku_id'); fields=row.get('inputs',[])
        if sku not in SKUS:raise ValueError('cabinet_editor_extra_sku')
        if sku in values or len(fields)!=1:raise ValueError('cabinet_editor_scope_changed')
        value=fields[0]['value'].strip()
        values[sku]=format(Decimal(value),'.2f') if value else None
    if set(values)!=set(SKUS):raise ValueError('cabinet_editor_incomplete')
    for sku,v in values.items():
        allowed={INITIAL[sku]}
        if sku in TARGETS:allowed.add(TARGETS[sku]['daily'])
        if v not in allowed:raise ValueError('cabinet_existing_price_drift')
    return values


def adjustment(observed,wanted):
    if (observed.get('item')!=ITEM or observed.get('requested_scope_verified') is not True
        or any(observed.get('window',{}).get(k)!=v for k,v in dict(WINDOW,offer_id=OFFER).items())):
        raise ValueError('cabinet_discount_identity_changed')
    values=observed.get('values',{}); missing=observed.get('not_enrolled',[])
    if set(values)&set(missing) or set(values)|set(missing)!=set(SKUS):
        raise ValueError('cabinet_discount_membership_incomplete')
    original=source()['original_offer_readback']['values']; changes={}
    for sku,new in wanted.items():
        if sku not in values:raise ValueError('cabinet_previously_saved_discount_missing')
        before=Decimal(values[sku])
        if before not in (Decimal(original[sku]),Decimal(new)):
            raise ValueError('cabinet_current_deduction_drift')
        if before!=Decimal(new):changes[sku]=dict(old=values[sku],new=new)
    return changes


def missing_discounts(observed,wanted):
    if adjustment(observed,wanted):raise ValueError('cabinet_discount_amend_not_verified')
    return {}


def final_acceptance(rows,expected):
    from campaign_final_audit import ACCEPTED
    selected=[]
    for sku in SKUS:
        found=[r for r in rows if r['item']==ITEM and r['sku']==sku and r['state'] not in ('撤销报名','已撤销','已取消')]
        if len(found)!=1:raise ValueError('cabinet_final_scope_not_unique')
        r=found[0]
        if r['marketing_id']!=MARKETING or r['state'] not in ACCEPTED or Decimal(r['activity_price'])!=Decimal(expected[sku]):
            raise ValueError('cabinet_final_status_or_price_unverified')
        selected.append(r)
    return dict(all_registered=True,registered_skus=len(selected),official_rows=selected)

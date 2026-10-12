"""Reconcile existing offers against a sub-window, without changing any offer.

Local saved official reads + frozen ERP only. This proves the conditional
discount arithmetic, not current enrollment or the future checkout price.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from campaign_generate_current_files import official_cut
from campaign_saved_inventory_audit import saved_job
from campaign_continuous_policy import fingerprint
from campaign_continuous_transport import persist


def audit(snapshot,read,items,window):
    index={}
    for erp in snapshot['all_erp_rows']:
        for sku in [erp.get('sku')]+list(erp.get('alt') or []):
            if sku:index.setdefault((str(erp['item']),str(sku)),{})[erp['code']]=erp
    rows=[];issues=[]
    for item in items:
        source=[r for r in read['rows'] if r['item']==item]
        if (len(source)!=2 or {r.get('mode') for r in source}!={'商品级','SKU级'}
                or not all(r.get('list_coverage_verified') is True for r in source)):
            issues.append(dict(item=item,reason='offer_list_not_verified'));continue
        offers=[o for r in source for o in r['offers'] if o['start']<=window['end'] and o['end']>=window['start']]
        if len(offers)!=1 or not (offers[0]['start']<=window['start']<=window['end']<=offers[0]['end']):
            issues.append(dict(item=item,reason='overlap_or_window_gap'));continue
        offer=offers[0]
        if not offer.get('sku_amounts_verified') or not offer.get('requested_scope_verified'):
            issues.append(dict(item=item,reason='amount_scope_not_verified'));continue
        for sku,amount in offer.get('values',{}).items():
            matches=list(index.get((item,sku),{}).values())
            if len(matches)!=1 or matches[0].get('custom') is not False:
                issues.append(dict(item=item,sku=sku,reason='ordinary_erp_identity_missing'));continue
            erp=matches[0];daily=Decimal(erp['daily']);target=Decimal(erp['medium_target'])
            final=daily-official_cut(daily,Decimal('.10'))-Decimal(amount)
            rows.append(dict(item=item,sku=sku,offer_id=offer['offer_id'],erp_code=erp['code'],
                start=offer['start'],end=offer['end'],window_contains_target=True,
                daily=str(daily),deduction=amount,target=str(target),conditional_final=str(final),
                delta=str(final-target),exact=final==target,within_two_yuan=abs(final-target)<=2,
                no_new_offer_needed=abs(final-target)<=2))
    return dict(rows=rows,issues=issues,platform_write=False,offers_unchanged=True,
                conditional_on_activity_price_equal_erp_daily=True,
                current_enrollment_not_proven=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--db',type=Path,required=True);p.add_argument('--job',required=True)
    p.add_argument('--items',nargs='+',required=True);p.add_argument('--start',required=True)
    p.add_argument('--end',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    job=saved_job(a.db,a.job)
    if job['state']!='finished' or job['operation']!='discount_item_discovery':raise ValueError('official_read_required')
    value=audit(json.loads(a.snapshot.read_text(encoding='utf-8')),job['result'],a.items,dict(start=a.start,end=a.end))
    value.update(source_job=a.job,source_result_sha256=fingerprint(job['result']))
    persist(a.output,value)
    print(json.dumps(dict(rows=len(value['rows']),exact=sum(r['exact'] for r in value['rows']),issues=value['issues'],output=str(a.output)),ensure_ascii=False))

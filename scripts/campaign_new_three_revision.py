"""Exact 2026-10-10 user grant; three products, October88 deductions only."""
from decimal import Decimal
from pathlib import Path
import campaign_cost_revision as core
from campaign_entry_authority import load, file_sha
from campaign_generate_current_files import official_cut
from campaign_price_snapshot import digest

ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-new-three-20261010')
PREFIX='new-three-oct88-user-approved-20261010:'
ITEMS=('1090473184978','1089439705938','1089444961055')
OFFER='149905944931'
CAMPAIGN='49594/49600/3548822475'
WINDOW={'start':'2026-10-07 20:00:00','end':'2026-10-12 23:59:59'}
PINS={'snapshot.json':'110ea59c2fa26db0c865df7a2503a77f0e6b21f792c963c4dcdd5cc1f01726d7',
      'oct88-discount-read.json':'f9720e106bdaf9b66d95ad72e9b631e755bcded366aa6444770e9203457c0281'}

def inputs():
    for name,sha in PINS.items():
        if file_sha(ROOT/name)!=sha:raise ValueError('new_three_pinned_evidence_changed')
    snapshot=load(ROOT/'snapshot.json')
    if digest(snapshot['all_erp_rows'])!=snapshot['resolved_price_version_sha256']:
        raise ValueError('new_three_snapshot_digest_changed')
    job=load(ROOT/'oct88-discount-read.json');read=job['result']
    if job['state']!='finished' or job['operation']!='discount_item_discovery' or read.get('state')!='readback' or read.get('platform_write') is not False or read['price_window']!=WINDOW:
        raise ValueError('new_three_original_read_incomplete')
    raw=load(read['evidence_path'])
    if any(raw.get(k)!=read.get(k) for k in ('rows','items','price_window','sku_scope','platform_write')):
        raise ValueError('new_three_recorded_read_changed')
    return snapshot,read

class Policy:
    OFFER=OFFER
    CAMPAIGN=CAMPAIGN
    WINDOW=WINDOW
    def __init__(self,item):
        if item not in ITEMS:raise ValueError('new_three_item_outside_user_grant')
        self.ITEM=item;self.GRANT=PREFIX+item
        snapshot,_=inputs()
        self.VERSION=snapshot['resolved_price_version_sha256']
        self.SKUS=tuple(sorted(str(r['sku']) for r in snapshot['all_erp_rows'] if r.get('item')==item and r.get('custom') is False))
        if len(self.SKUS)!=(9 if item==ITEMS[0] else 6):raise ValueError('new_three_ordinary_scope_changed')
    def derive(self):
        snapshot,read=inputs()
        product=[r for r in read['rows'] if r['item']==self.ITEM and r['mode']=='商品级']
        groups=[r for r in read['rows'] if r['item']==self.ITEM and r['mode']=='SKU级']
        if len(product)!=1 or product[0]['offers'] or len(groups)!=1:
            raise ValueError('new_three_offer_modes_or_overlap')
        overlaps=[o for o in groups[0]['offers'] if o.get('overlaps_requested_window')]
        if len(overlaps)!=1:raise ValueError('new_three_offer_not_unique')
        offer=overlaps[0]
        if offer['offer_id']!=OFFER or any(offer[k]!=v for k,v in WINDOW.items()) or offer.get('sku_amounts_verified') is not True:
            raise ValueError('new_three_exact_saved_amounts_required')
        rows=[]
        for sku in self.SKUS:
            matches=[r for r in snapshot['all_erp_rows'] if (r.get('item'),r.get('sku'))==(self.ITEM,sku) and r.get('custom') is False]
            if len(matches)!=1:raise ValueError('new_three_mapping_not_unique')
            erp=matches[0];daily=Decimal(erp['daily']);target=Decimal(erp['big_target'])
            old=Decimal(offer['values'][sku]);new=daily-official_cut(daily,Decimal('.12'))-target
            if any(not v.is_finite() or v<0 or v!=v.quantize(Decimal('.01')) for v in (daily,target,old,new)) or target<=0:
                raise ValueError('new_three_invalid_amount')
            rows.append(dict(item=self.ITEM,sku=sku,offer_id=OFFER,erp_code=erp['code'],daily=str(daily),target=str(target),final=str(target),old_deduct=str(old),new_deduct=str(new),deduct=str(new)))
        return dict(grant_id=self.GRANT,campaign=CAMPAIGN,shop_name='畔色木作',**WINDOW,price_version=self.VERSION,rows=rows,
            sources=[{'path':str(ROOT/n),'sha256':h} for n,h in PINS.items()],
            automatic_retry=False,rotation_performed=False,user_approval='2026-10-10 user allowed correcting these three October88 deductions to ERP big target; Double11 excluded')
    def check_current(self,a,body):
        return core.check_current(a,body,policy=self)
    def validate_proof(self,body,cid,jid,proof):
        return core.validate_proof(body,cid,jid,proof,policy=self)

def engine(grant):
    if not grant.startswith(PREFIX):raise ValueError('new_three_grant_unknown')
    return Policy(grant[len(PREFIX):])

def unchanged_custom_reuse(row,actual,offer,campaign,start,end):
    """Preserve the user's existing custom deduction; never lower its signup price."""
    if campaign!=CAMPAIGN or (start,end)!=(WINDOW['start'],WINDOW['end']) or row['item'] not in ITEMS or offer.get('platform_offer_id',offer['offer_id'])!=OFFER:
        return None
    snapshot,read=inputs()
    matches=[r for r in snapshot['all_erp_rows'] if (r.get('item'),r.get('sku'))==(row['item'],row['sku']) and r.get('custom') is True]
    if len(matches)!=1 or len(actual)!=1 or Decimal(row['activity_price'])!=Decimal(matches[0]['daily']):
        raise ValueError('new_three_custom_signup_price_must_remain_erp_daily')
    values=[o['values'][row['sku']] for r in read['rows'] if r['item']==row['item'] and r['mode']=='SKU级' for o in r['offers'] if o['offer_id']==OFFER]
    if len(values)!=1 or Decimal(actual[0]['deduct'])!=Decimal(values[0]):
        raise ValueError('new_three_custom_existing_deduction_changed')
    return {'review':'user_existing_custom_deduction_preserved_no_lowering','source_sha256':PINS['oct88-discount-read.json'],'actual_deduct':values[0]}

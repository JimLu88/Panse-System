import copy
from datetime import datetime
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_two_product_prepare as m

NOW=datetime(2026,10,3,0,0,tzinfo=m.TZ)
START='2026-10-03 03:00:00'

def test_exact_current_evidence():
    rows,discounts,records,scope=m.validate(m.sources(),START,NOW)
    assert len(rows)==18 and sum(r['preserved'] for r in rows)==14
    assert {r['sku']:r['deduct'] for r in discounts}==m.DEDUCT
    assert {r['sku']:r['activity_price'] for r in rows if not r['preserved']}==m.NEW
    assert {r['sku']:r['activity_price'] for r in rows if r['sku'] in ('5193229462949','5193229462950')}=={'5193229462949':'3388.50','5193229462950':'3624.00'}
    assert len(scope['sku_facts'])==18 and len(records)==2

@pytest.mark.parametrize('kind', ['running','write','extra_item','not_abnormal','missing_row','duplicate_sku','missing_erp','wrong_daily','active_new_discount','wrong_offer','wrong_end','drift_target'])
def test_reject_bad_evidence(kind):
    d=m.sources();s,e,o,f=(d[n] for n in m.PINS)
    product=o['job']['result']['rows'][0]
    if kind=='running': o['job']['state']='running'
    elif kind=='write': o['platform_write']=True
    elif kind=='extra_item': o['job']['result']['rows'].append(copy.deepcopy(product))
    elif kind=='not_abnormal': product['list_rows'][0]['cells'][5]='活动中'
    elif kind=='missing_row': product['editor_inventory']['rows'].pop()
    elif kind=='duplicate_sku': product['editor_inventory']['rows'][1]=copy.deepcopy(product['editor_inventory']['rows'][0])
    elif kind=='missing_erp': e['all_erp_rows']=[]
    elif kind=='wrong_daily':
        for r in e['all_erp_rows']:
            if r['code']=='PPS2321003020211': r['daily']='1.00'
    elif kind=='active_new_discount':
        next(r for r in f['active_offers'] if r['item']=='722275846168')['values']['6299575587331']='1.00'
    elif kind in ('wrong_offer','wrong_end'):
        r=next(r for r in f['active_offers'] if r['item']=='722275846168');r['offer_id' if kind=='wrong_offer' else 'end']='changed'
    elif kind=='drift_target':
        for r in e['all_erp_rows']:
            if r['code']=='PPS2441004051315':r['medium_target']='1.00'
    with pytest.raises(ValueError):m.validate(d,START,NOW)

@pytest.mark.parametrize('start',['2026-10-02 00:00:00',m.END,'2026-10-08 00:00:00'])
def test_window(start):
    with pytest.raises(ValueError): m.validate(m.sources(),start,NOW)

def test_shared_reservation_blocks_overlap(tmp_path):
    rows,_,records,_=m.validate(m.sources(),START,NOW)
    request={'campaign':m.CAMPAIGN,'marketing_records':records}
    m.reserve(request,'test',rows,tmp_path/'ledger')
    with pytest.raises(ValueError,match='already_prepared_or_unknown'):
        m.reserve(request,'test2',rows,tmp_path/'ledger')

from copy import deepcopy
import pytest
from campaign_three_discount_overlay import apply_entries,WINDOWS

def fixture():
    oid,start,end=WINDOWS['super'];item='1090473184978'
    offer=dict(offer_id=oid,start=start,end=end,items=[dict(item=item,status='success'),dict(item='other',status='success')],rows=[dict(item=item,sku='0',deduct='99'),dict(item='other',sku='kept',deduct='456')])
    wanted={str(n):'100' for n in range(9)}
    entry=dict(campaign_name='super',item=item,offer_id=oid,start=start,end=end,state='verified_saved',wanted=wanted,after=dict(item=item,values=wanted,window=dict(offer_id=oid,start=start,end=end)))
    return [offer],[entry]

def test_preserves_history_input_and_other_product():
    offers,entries=fixture();before=deepcopy(offers);result=apply_entries(offers,entries)
    assert offers==before
    assert [r for r in result[0]['rows'] if r['item']=='other']==[r for r in before[0]['rows'] if r['item']=='other']
    assert result[0]['rows'][0]['deduct']=='100'

@pytest.mark.parametrize('fault',['amount','window','count','unknown'])
def test_rejects_unverified(fault):
    offers,entries=fixture();entry=entries[0];entry['after']=deepcopy(entry['after'])
    if fault=='amount':entry['after']['values']['0']='1'
    if fault=='window':entry['end']='2026-10-21 19:59:59'
    if fault=='count':entry['wanted'].pop('0')
    if fault=='unknown':offers[0]['items'][0]['status']='unknown'
    with pytest.raises(ValueError):apply_entries(offers,entries)

from copy import deepcopy
import pytest
from campaign_saved_discount_scope_audit import audit

WINDOW=dict(start='2026-10-13 00:00:00',end='2026-10-16 23:59:59')
def fixture():
    s={'all_erp_rows':[dict(item='1',sku='2',alt=[],code='P1',custom=False,daily='8257.50',medium_target='5150.00')]}
    o=dict(offer_id='3',start=WINDOW['start'],end='2026-10-20 19:59:59',sku_amounts_verified=True,
        requested_scope_verified=True,values={'2':'2281.50'})
    r={'rows':[dict(item='1',mode='商品级',list_coverage_verified=True,offers=[]),
        dict(item='1',mode='SKU级',list_coverage_verified=True,offers=[o])]}
    return s,r

def test_containing_window_and_official_whole_yuan_rounding():
    s,r=fixture();out=audit(s,r,['1'],WINDOW)
    assert out['issues']==[] and out['rows'][0]['exact']
    assert out['offers_unchanged'] and out['conditional_on_activity_price_equal_erp_daily']

@pytest.mark.parametrize('bad',['missing_mode','overlap','gap','unverified','mapping'])
def test_uncertain_evidence_does_not_pass(bad):
    s,r=fixture()
    if bad=='missing_mode':r['rows'].pop(0)
    if bad=='overlap':r['rows'][0]['offers'].append(deepcopy(r['rows'][1]['offers'][0]))
    if bad=='gap':r['rows'][1]['offers'][0]['start']='2026-10-14 00:00:00'
    if bad=='unverified':r['rows'][1]['offers'][0]['sku_amounts_verified']=False
    if bad=='mapping':s['all_erp_rows']=[]
    out=audit(s,r,['1'],WINDOW)
    assert out['issues'] and out['rows']==[]

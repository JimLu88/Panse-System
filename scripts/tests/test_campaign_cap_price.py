from decimal import Decimal as D
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_cap_price import ordinary_price
from campaign_discount_reuse import reconcile


@pytest.mark.parametrize('cap,expected', [('800','800'),('799.90','799.90'),('798','798'),('900','800'),('799.999','799.99')])
def test_exact_original_target_and_cap(cap,expected):
    r=ordinary_price('1000','800','.1',cap)
    assert D(r['final'])==D(expected)
    assert r['target']=='800' and D(r['final'])<=D(cap)
    assert D(r['daily'])-D(r['official_cut'])-D(r['deduct'])==D(r['final'])


@pytest.mark.parametrize('cap', ['797.99','0','-1','NaN','Infinity',None,'','bad'])
def test_invalid_or_over_two_not_silently_accepted(cap):
    with pytest.raises(ValueError):ordinary_price('1000','800','.1',cap)


def test_cent_rounding_and_no_ratchet():
    assert ordinary_price('30','20.41','.1','20.40')['deduct']=='6.60'
    with pytest.raises(ValueError,match='rotation'):ordinary_price('1000','800','.1','797.90')


def test_saved_deduction_must_also_meet_cap():
    row=dict(item='123456789',sku='987654321',custom=False,activity_price='1000',target='800',big_target='700',platform_cap='799.9',cap_tolerance='2')
    planned=[dict(row,daily='1000',deduct='100.10')]
    offer=dict(offer_id='1234567891',start='2026-09-28 00:00:00',end='2026-09-30 23:59:59',items=[dict(item=row['item'],status='success')],rows=[dict(item=row['item'],sku=row['sku'],deduct='100')])
    args=([row],planned,[offer],offer['start'],offer['end'],D('.1'))
    _,reused,issues=reconcile(*args)
    assert not reused and issues[0]['error']=='actual_reused_discount_exceeds_current_platform_cap'
    offer['rows'][0]['deduct']='100.10'
    remaining,reused,issues=reconcile(*args)
    assert not issues and not remaining and reused[0]['final']=='799.90'
    offer['items'][0]['status']='unknown'
    assert reconcile(*args)[2][0]['error']=='existing_discount_outcome_unknown'


def test_wrong_window_overlap_not_relaxed():
    row=dict(item='123456789',sku='987654321',custom=False,activity_price='1000',target='800',big_target='700',platform_cap='799.9')
    offer=dict(offer_id='1234567891',start='2026-09-28 00:00:00',end='2026-10-07 19:59:59',items=[dict(item=row['item'],status='success')],rows=[])
    assert reconcile([row],[],[offer],offer['start'],'2026-09-30 23:59:59',D('.1'))[2][0]['error']=='existing_discount_window_not_exact'


def test_real_generation_consumer_preserves_custom_and_original_daily():
    from test_campaign_current_rate import TwoFilePriceRowsTest
    from campaign_generate_current_files import build_rows
    f=TwoFilePriceRowsTest();f.setUp()
    pair=(f.erp[0]['item'],f.erp[0]['sku'])
    a,d,issues=build_rows(f.snapshot,f.identities,D('.1'),'medium',f.bases,platform_caps={pair:'74.90'})
    assert not issues and len(d)==1 and len(a)==2
    assert d[0]['target']=='75.00' and d[0]['final']=='74.90'
    assert a[0]['activity_price']=='100.00' and a[1]['activity_price']=='1700.00'
    a,d,issues=build_rows(f.snapshot,f.identities,D('.1'),'medium',f.bases,platform_caps={pair:'72.99'})
    assert not d and len(a)==1 and issues[0]['error'].endswith('requires_rotation')
    a,d,issues=build_rows(f.snapshot,f.identities,D('.1'),'medium',f.bases,platform_caps={})
    assert not d and issues[0]['error']=='current_platform_cap_missing'

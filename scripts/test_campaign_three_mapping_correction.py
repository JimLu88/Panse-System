from copy import deepcopy
import pytest
from campaign_three_mapping_correction import validate, protected


def fixture():
    baseline=[dict(code=str(i),sku=str(i+100),item='7',alt=[],daily='100',medium_target='80',big_target='70') for i in range(24)]
    payload=dict(baseline=baseline,new_by_code={'0':'101','1':'100'})
    state=dict(prices=[dict(sku_code=r['code'],daily_price='100') for r in baseline],
               promos=[dict(sku_code=r['code'],taobao_item_id='7',taobao_sku_id=r['sku'],alt_taobao_sku_ids=[],mid_buyer_price='80',big_buyer_price='70') for r in baseline],
               listings=[dict(taobao_item_id='7',taobao_sku_id=r['sku'],sku_code=r['code']) for r in baseline])
    return state,payload


def test_closed_permutation():
    state,payload=fixture();validate(state,payload)
    after=deepcopy(state)
    for row in after['promos']:row['taobao_sku_id']=payload['new_by_code'].get(row['sku_code'],row['taobao_sku_id'])
    after['listings'][0]['sku_code']='1';after['listings'][1]['sku_code']='0'
    validate(after,payload,applied=True)
    assert protected(state)==protected(after)


@pytest.mark.parametrize('change',['price','mapping','floor','listing','scope','newid'])
def test_reject_drift(change):
    state,payload=fixture()
    if change=='price':state['prices'][0]['daily_price']='99'
    if change=='mapping':state['promos'][0]['taobao_sku_id']='unknown'
    if change=='floor':state['promos'][0]['coupon_floor_price']='80'
    if change=='listing':state['listings'][0]['sku_code']='other'
    if change=='scope':state['promos'].pop()
    if change=='newid':payload['new_by_code']['0']='900'
    with pytest.raises(ValueError):validate(state,payload)

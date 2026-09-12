import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_continuous_flow import repair_signature,repair_already_attempted
from campaign_continuous_policy import fingerprint


def test_equal_price_other_sku_is_not_a_repeat_and_old_sku_remains_protected():
    first={'sku':'11','repair':{'kind':'custom_price','price':'396.99','exact_floor':'100'}}
    sibling=dict(first,sku='12')
    state={'repairs_seen':{'1':[fingerprint([first['repair']])]},'corrections':{'1':[first]}}
    assert repair_already_attempted(state,'1',[first])
    assert not repair_already_attempted(state,'1',[sibling])
    state['repairs_seen']['1'].append(repair_signature([sibling]))
    assert repair_already_attempted(state,'1',[sibling])
    assert repair_signature([first,sibling])==repair_signature([sibling,first])

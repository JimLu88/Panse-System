import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest
spec=importlib.util.spec_from_file_location('partial_amend',Path(__file__).parents[1]/'campaign_partial_amend_recovery.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def fixture():
    body={'start':'start','end':'end','rows':[dict(offer_id='offer',item=str(i),sku=str(i+10),old_deduct='10',new_deduct='10.5') for i in range(4)]}
    rows=[dict(item=str(i),values={str(i+10):'10.5' if i<2 else '10'},window=dict(offer_id='offer',start='start',end='end')) for i in range(4)]
    return body,rows,[('offer','0')],2

def test_only_never_attempted_tail_can_be_dispatched():
    a,b,c,d=fixture();applied,remaining=m.partition(a,b,c,d)
    assert [r['item'] for r in applied]==['0','1']
    assert [r['item'] for r in remaining]==['2','3']

@pytest.mark.parametrize('fault',['prefix_gap','save_count','unknown_not_saved','tail_already_changed','mixed_amount','missing_sku','duplicate_item','window','missing_item','not_enrolled'])
def test_ambiguous_or_changed_stops_before_any_write(fault):
    a,b,c,d=fixture()
    if fault=='prefix_gap':c=[('offer','2')]
    if fault=='save_count':d=3
    if fault=='unknown_not_saved':b[1]['values']['11']='10'
    if fault=='tail_already_changed':b[2]['values']['12']='10.5'
    if fault=='mixed_amount':b[1]['values']['11']='10.2'
    if fault=='missing_sku':b[0]['values']={}
    if fault=='duplicate_item':b.append(deepcopy(b[0]))
    if fault=='window':b[0]['window']['end']='other'
    if fault=='missing_item':b.pop()
    if fault=='not_enrolled':b[0]['not_enrolled']=['10']
    with pytest.raises(ValueError):m.partition(a,b,c,d)

import copy
import pytest
from campaign_three_rotation_mapping import validate,ITEMS

def fixture():
    promos=[];rows=[]
    for item,count,changed in zip(ITEMS,[10,7,7],[1,7,7]):
        for n in range(count):
            code=f'{item}-{n}';sid=f'old-{code}'
            promos.append({'sku_code':code,'taobao_sku_id':sid})
            rows.append({'sku_code':code,'old_sku_id':sid,'taobao_sku_id':f'new-{code}' if n<changed else sid,'taobao_item_id':item,'changed':n<changed})
    return {'ok':True,'mappings':rows},{'promos':promos}

def test_exact_scope():validate(*fixture())

@pytest.mark.parametrize('fault',['scope','drift','count','duplicate','failed'])
def test_rejects_mapping_fault(fault):
    report,prior=fixture()
    if fault=='scope':report['mappings'].pop()
    if fault=='drift':report['mappings'][0]['old_sku_id']='other'
    if fault=='count':report['mappings'][0]['changed']=False
    if fault=='duplicate':report['mappings'][1]['taobao_sku_id']=report['mappings'][0]['taobao_sku_id']
    if fault=='failed':report['ok']=False
    with pytest.raises(ValueError):validate(report,prior)

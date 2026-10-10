import pytest
from campaign_enamel_final_mapping import validate,ITEM,CODE,OLD
def sample():
 prior=[{'sku_code':CODE if n==0 else 'code'+str(n),'taobao_sku_id':OLD if n==0 else str(n)} for n in range(10)]
 rows=[{'sku_code':r['sku_code'],'taobao_item_id':ITEM,'old_sku_id':r['taobao_sku_id'],'taobao_sku_id':'new' if n==0 else r['taobao_sku_id'],'changed':n==0} for n,r in enumerate(prior)]
 return {'ok':True,'mappings':rows},prior
def test_exact_change():validate(*sample())
@pytest.mark.parametrize('fault',['scope','other','drift'])
def test_stops(fault):
 r,p=sample()
 if fault=='scope':r['mappings'].pop()
 if fault=='other':r['mappings'][1]['changed']=True
 if fault=='drift':r['mappings'][2]['old_sku_id']='wrong'
 with pytest.raises(ValueError):validate(r,p)

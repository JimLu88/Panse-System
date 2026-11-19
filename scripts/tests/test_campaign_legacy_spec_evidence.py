from copy import deepcopy
import json
import pytest
import campaign_legacy_spec_evidence as mod


def example():
    mapping=dict(item='720234422814',sku='5187774106112',erp_code='PPS2316001040102',official_sku_code='2316001040102',repair_kind='verified_bound_legacy_prefix')
    fact=dict(item=mapping['item'],sku=mapping['sku'],sku_code=mapping['official_sku_code'],attributes='适用人数:双人位2米;颜色分类:奶霜白;')
    row=dict(product_code='PPS23160010401',code=mapping['erp_code'],item=mapping['item'],sku=mapping['sku'],custom=False,sku_name='2023款沙发-200 双人半位-奶霜白')
    return mapping,fact,row,({'restored':[deepcopy(mapping)]},{(mapping['item'],mapping['sku']):deepcopy(fact)})


def test_reviewed_half_seat_and_exact_dimensions():
    assert mod.matches(*example())


@pytest.mark.parametrize('target,key,value',[
    ('row','custom',True),('row','sku','other'),('row','item','other'),('row','code','other'),('row','product_code','other'),
    ('row','sku_name','2023款沙发-210 双人半位-奶霜白'),('row','sku_name','2023款沙发-200 双人半位-灰色'),
    ('row','sku_name','2023款沙发-200 三人位-奶霜白'),('row','sku_name','200 双人半位-奶霜白'),
    ('fact','sku_code','other'),('fact','attributes','适用人数:双人位2.1米;颜色分类:奶霜白;'),
    ('mapping','erp_code','other'),('mapping','sku','other')])
def test_no_guessing_new_identity_or_spec(target,key,value):
    mapping,fact,row,proof=example()
    {'row':row,'fact':fact,'mapping':mapping}[target][key]=value
    assert not mod.matches(mapping,fact,row,proof)


def test_missing_history_rejected():
    mapping,fact,row,proof=example()
    assert not mod.matches(mapping,fact,row,None)
    assert not mod.matches(mapping,fact,row,({'restored':[]},proof[1]))


def test_tampered_historical_source_rejected(tmp_path,monkeypatch):
    p=tmp_path/'history.json';p.write_text('{}')
    monkeypatch.setattr(mod,'HISTORY_SHA','expected')
    with pytest.raises(ValueError,match='source_changed'):
        mod.history({'sources':[{'path':str(p),'sha256':'expected'}]})

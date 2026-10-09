import importlib.util
import sys
from pathlib import Path
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
spec=importlib.util.spec_from_file_location('shipping_cell',Path(__file__).parents[1]/'campaign_shipping_cell_recovery.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture(monkeypatch):
    proof={'item':'1','skus':['10','11'],'template':{'path':'template'},'submitted':{'path':'submitted'}}
    data={'template':{'10':'','11':''},'submitted':{'10':'30天','11':'30天'}}
    monkeypatch.setattr(m,'cells',lambda ref,item:data[ref['path']])
    return proof,data


def test_exact_native_blank_can_be_restored(monkeypatch):
    proof,data=fixture(monkeypatch)
    assert m.validate(proof,'1',['10','11'])


@pytest.mark.parametrize('fault',['item','scope','missing_native','missing_sent','extra_sent','native_nonblank','already_blank'])
def test_changed_or_non_generated_scope_rejected(monkeypatch,fault):
    p,d=fixture(monkeypatch)
    if fault=='item':p['item']='2'
    if fault=='scope':p['skus']=['10']
    if fault=='missing_native':d['template'].pop('11')
    if fault=='missing_sent':d['submitted'].pop('11')
    if fault=='extra_sent':d['submitted']['12']='30天'
    if fault=='native_nonblank':d['template']['10']='30天'
    if fault=='already_blank':d['submitted']['10']=''
    with pytest.raises(ValueError):m.validate(p,'1',['10','11'])


def test_other_official_message_never_grants_clear(monkeypatch):
    errors=[{'message':'该商品需要包邮','sku':'','terminal':'failed','official_evidence':{'sha256':'x'}}]
    assert m.annotate(errors,{})==errors


def test_file_hash_checked_before_parse(tmp_path):
    p=tmp_path/'source.xlsx';p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='source_changed'):m.cells({'path':str(p),'sha256':'x'},'1')

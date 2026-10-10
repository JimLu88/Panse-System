import json,hashlib
from copy import deepcopy
import pytest
from campaign_mapping_supersession import apply

def sample(tmp_path):
 p=tmp_path/'proof.json';p.write_text(json.dumps({'state':'rotation_mapping_verified','erp_prices_unchanged':True,'readback':{'mappings':[{'sku_code':'code','taobao_sku_id':'2'}]}}))
 old={'kind':'mapping','sha256':'old','document':{'restored':[{'item':'1','erp_code':'code','sku':'1'}]}}
 new={'kind':'mapping','sha256':'new','document':{'status':'verified_partial_mapping_restored','supersedes_mapping_sha256':'old','restored':[{'item':'1','erp_code':'code','sku':'2'}],'evidence':{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}}}
 return [old,new]

def test_current_replaces_not_mutates(tmp_path):
 s=sample(tmp_path);old=deepcopy(s);assert apply(s)==[s[1]];assert s==old

@pytest.mark.parametrize('change',['scope','sku','proof','predecessor'])
def test_rejects_drift(tmp_path,change):
 s=sample(tmp_path);d=s[1]['document']
 if change=='scope':d['restored'][0]['item']='3'
 if change=='sku':d['restored'][0]['sku']='4'
 if change=='proof':d['evidence']['sha256']='bad'
 if change=='predecessor':d['supersedes_mapping_sha256']='missing'
 with pytest.raises(ValueError):apply(s)

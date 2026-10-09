import hashlib
import json
from pathlib import Path
import importlib.util
import sys
import pytest

sys.path.insert(0,'D:/AI/畔色ERP系统/ERP程序/scripts')
spec=importlib.util.spec_from_file_location('fresh',Path(__file__).parents[1]/'scripts/campaign_fresh_reference_supplement.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

@pytest.fixture
def data(tmp_path,monkeypatch):
    raw=tmp_path/'reference.xlsx';raw.write_bytes(b'fixture')
    receipt=tmp_path/'receipt.json'
    identity=dict(campaign_id='1',phase_id='2',sign_record_id='3',start='a',end='b')
    value=dict(identity=identity,state='succeeded',tool_id='activity_template',result=dict(
        source='official_current_template_download',business_write=False,path=str(raw),
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),items=['10']))
    receipt.write_text(json.dumps(value))
    descriptor=dict(receipt_path=str(receipt),receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),items=['10'])
    body=dict(campaign='1/2/3',start='a',end='b',signup_rows=[dict(item='10',sku='20',activity_price='100')])
    error=dict(item='10',sku='',kind='unknown',terminal='failed',batch='5',official_evidence={'sha256':'original'},
        parse_issue='unparsed_or_incomplete_official_failure',message='活动价格由卖家 您的sku：规格 在管')
    monkeypatch.setattr(m,'read_rows',lambda *a:{2:dict(A='商品ID',E='SKUID',H='最低标价',I='最低普惠券后价要求'),
        4:dict(A='10',E='20',H='90',I='70')})
    return dict(errors=[error]),body,descriptor

def test_supplement_preserves_original(data):
    terminal,body,descriptor=data
    result=m.supplement(*data)
    assert len(result['errors'])==2
    assert {r['kind'] for r in result['errors']}=={'list_price','coupon_price'}
    assert result['errors'][0]['supplemental_evidence']['original_errors']==terminal['errors']
    assert terminal['errors'][0]['kind']=='unknown'

def test_campaign_mismatch(data):
    data[1]['campaign']='1/2/4'
    with pytest.raises(ValueError,match='identity'):m.supplement(*data)

def test_receipt_changed(data):
    Path(data[2]['receipt_path']).write_text('{}')
    with pytest.raises(ValueError,match='receipt_changed'):m.supplement(*data)

def test_other_error_retained(data):
    data[0]['errors'].append(dict(data[0]['errors'][0],parse_issue='free_shipping_commitment_required'))
    assert m.supplement(*data)==data[0]

def test_missing_sku_keeps_unknown(data):
    data[1]['signup_rows'].append(dict(item='10',sku='21',activity_price='100'))
    assert m.supplement(*data)==data[0]

def test_success_not_reclassified(data):
    data[0]['errors'][0]['terminal']='success'
    assert m.supplement(*data)==data[0]

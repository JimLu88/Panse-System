import copy
import hashlib
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_authorized_item_scope import validate,apply,AUTHORIZATION


@pytest.fixture
def scope(tmp_path):
    window=dict(campaign='1/2/3',start='2027-01-01 00:00:00',end='2027-01-02 23:59:59')
    source=dict(schema='campaign-user-scope-receipt-v1',operation='signup_only',authorization='current-user-instruction',
                items=['123456'],windows=[window],shop_id='shop')
    raw=json.dumps(source).encode();path=tmp_path/'user.json';path.write_bytes(raw)
    return dict(schema='campaign-item-scope-v2',**{k:source[k] for k in ('authorization','items','windows','shop_id')},
                source=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()))


def test_new_product_and_activity_not_hardcoded(scope):
    plan=dict(segments=[dict(campaign='1/2/3',shop_id='shop',price_window={k:scope['windows'][0][k] for k in ('start','end')})])
    assert validate(scope,plan)==scope


@pytest.mark.parametrize('field,value',[('items',['999']),('authorization','old'),('shop_id','other')])
def test_scope_cannot_drift_from_user_instruction(scope,field,value):
    scope[field]=value
    with pytest.raises(ValueError):validate(scope)


def test_different_window_rejected(scope):
    scope['windows'][0]['end']='2027-01-03 23:59:59'
    with pytest.raises(ValueError):validate(scope)


def test_cannot_attach_rotation_permission(scope):
    scope['rotation']=True
    with pytest.raises(ValueError):validate(scope)


def test_plan_shop_must_match(scope):
    plan=dict(segments=[dict(campaign='1/2/3',shop_id='other',price_window={k:scope['windows'][0][k] for k in ('start','end')})])
    with pytest.raises(ValueError):validate(scope,plan)


def test_subset_preserves_all_source_facts(scope):
    data=dict(erp_sellable=['123456','777'],platform_rows=[dict(item=i,on_sale=True) for i in ['123456','777']],sku_facts=['unchanged'])
    result=apply(data,scope)
    assert result['erp_sellable']==['123456'] and result['sku_facts']==['unchanged']
    assert data['erp_sellable']==['123456','777']


def test_legacy_unchanged():
    value=dict(authorization=AUTHORIZATION,items=['1001358847694'])
    assert validate(value)==value
    with pytest.raises(ValueError):validate(dict(authorization=AUTHORIZATION,items=['123456']))


def test_not_on_sale_is_not_accepted(scope):
    with pytest.raises(ValueError):apply(dict(erp_sellable=['123456'],platform_rows=[dict(item='123456',on_sale=False)]),scope)


def test_source_hash_protected(scope):
    scope['source']['sha256']='0'*64
    with pytest.raises(ValueError):validate(scope)

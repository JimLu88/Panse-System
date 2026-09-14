from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_verified_failure_resubmit as r
from campaign_continuous_execute import validate_request


@pytest.mark.parametrize('change',[
    {'items':['793052650673']},{'price_change':True},{'discount_change':True},
    {'parent_request_id':'f'*64},{'schema':'other'},{'rule_sha':'f'*64},{'price':'1'},
])
def test_exact_scope_cannot_expand(change):
    with pytest.raises(ValueError):validate_request(dict(r.REQUEST,**change))


def sample():
    row={'SKUID':'1','商品状态':'异常','营销ID':'10030567401931','活动价':'19095',
         '最低标价':'19095','活动普惠券后价':'14083.07','最低普惠券后价要求':'14083.07'}
    sku={'sku':'1','activity_price':'19095','target':'14083.67','big_target':'13673.47','custom':False}
    return [row],[sku]


def test_current_equal_ceiling_requires_no_extra_ten_cents():
    rows,skus=sample()
    assert r.check_constraints(rows,skus)=={'1':skus[0]}
    assert validate_request(r.REQUEST)=='畔色木作'


@pytest.mark.parametrize('key,value',[
    ('活动普惠券后价','14083.17'),('活动价','19094'),('最低标价','18000'),
    ('商品状态','已发布设定'),('营销ID','other'),('活动普惠券后价','NaN'),
])
def test_changed_price_unresolved_constraint_and_success_are_rejected(key,value):
    rows,skus=sample();rows[0][key]=value
    with pytest.raises(ValueError):r.check_constraints(rows,skus)


def test_complete_scope_and_same_target_tolerance():
    rows,skus=sample()
    with pytest.raises(ValueError):r.check_constraints(rows*2,skus)
    rows[0]['活动普惠券后价']='14080'
    with pytest.raises(ValueError):r.check_constraints(rows,skus)


@pytest.mark.parametrize('aria,cls,enabled,wanted',[
    ('1','next-switch-on',None,True),('true','next-switch-on',True,True),
    ('false','next-switch-off',False,False),('0','next-switch-off',None,False),
])
def test_explicit_dom_switch_not_stock(aria,cls,enabled,wanted):
    assert r.switch_state({'enabled':enabled,'switches':[{'aria':aria,'class_name':cls}],'stock':0}) is wanted


@pytest.mark.parametrize('switch',[
    {'aria':'false','class_name':'next-switch-on'},
    {'aria':'1','class_name':'next-switch-off'},
    {'aria':'1','class_name':'next-switch-on next-switch-off'},
    {'aria':'','class_name':'next-switch-on'},
])
def test_ambiguous_switch_never_silently_excludes(switch):
    with pytest.raises(ValueError):r.switch_state({'enabled':None,'switches':[switch]})


def test_fixed_execute_uses_valid_read_id_and_cached_terminal(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import campaign_final_audit
    snap=r.persist(tmp_path/'snapshot.json',{})
    p={'bundle_id':'a'*64,'identity':{},'items':[r.ITEM],'time_binding':{'segment':{'segment_id':'b'*64}}}
    r.persist(tmp_path/'prepared.json',p)
    calls=[]
    class T:
        def __init__(self,*a,root,**kw):self.root=root
        def step_verify_discount_window(self,key,*a):
            assert len(key)==64 and all(c in '0123456789abcdef' for c in key)
            calls.append('read');return {'all_correct':True}
        def submit_phase(self,*a):
            calls.append('signup');return {'items':[{'item':r.ITEM,'outcome':'success'}],'batch':'123'}
    a=SimpleNamespace(db=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchone=lambda:{'status':'failed','evidence':'{"batch":"843589003"}'})))
    monkeypatch.setattr(r,'verify_prepared',lambda *a:{'snapshot_path':snap})
    monkeypatch.setattr(r,'CampaignTransport',T)
    monkeypatch.setattr(campaign_final_audit,'finalize',lambda request,result,**kw:result)
    r.execute(r.REQUEST,root=tmp_path,authority=a,edge=None,artifact_roots=[])
    r.execute(r.REQUEST,root=tmp_path,authority=a,edge=None,artifact_roots=[])
    assert calls==['read','signup']

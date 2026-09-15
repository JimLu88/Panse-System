import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import campaign_cabinet_closeout as mod
from campaign_continuous_transport import persist


def request():
    return dict(approved_cabinet_rotation=mod.AUTH,authorized_item_scope={'items':[mod.ITEM]},pages={mod.AUTUMN:{}})


@pytest.mark.parametrize('change',[None,'scope','export','authorization','activity'])
def test_exact_request_not_generic_rotation(change):
    r=request()
    if change=='scope':r['authorized_item_scope']['items'].append('1')
    if change=='export':r['existing_product_export']={'job_id':'old'}
    if change=='authorization':r['approved_cabinet_rotation']='old'
    if change=='activity':r['pages']={}
    if change:
        with pytest.raises(ValueError):mod.validate(r)
    else:mod.validate(r)


def facts_and_rows():
    pairs=[]
    for old,code,new,newcode,daily in mod.PAIRS:
        pairs.extend([(old,code,False),(new,newcode,True)])
    pairs.extend([(str(900+i),'PPSOTHER000000'+str(i),True) for i in range(3)])
    facts=[];rows=[]
    for sku,code,on in pairs:
        facts.append(dict(item=mod.ITEM,sku=sku,sku_code=code,attributes='颜色分类:'+sku,price='1000',stock='17'))
        rows.append(dict(index=len(rows),cells=[dict(text=sku,fields=[]),dict(text='',fields=[{'value':code}]),
            dict(text='元',fields=[{'value':'1000'}]),dict(text='件',fields=[{'value':'17'}])],
            switches=[dict(aria='true' if on else 'false',class_name='next-switch '+('next-switch-on' if on else 'next-switch-off'))]))
    return facts,rows


@pytest.mark.parametrize('bad',[None,'stock','price','code','switch','rows','daily','state'])
def test_publication_and_new_export_must_match_before_alias_binding(tmp_path,bad):
    facts,rows=facts_and_rows()
    if bad in ('stock','price'):facts[1][bad]='999'
    if bad=='code':facts[1]['sku_code']='PPSBAD'
    if bad=='switch':rows[0]['switches'][0]=dict(aria='true',class_name='next-switch next-switch-on')
    if bad=='rows':rows.pop()
    state='unknown' if bad=='state' else 'published_verified'
    persist(tmp_path/'cabinet-publication.json',dict(state='finished',result=dict(state=state,authorization=mod.AUTH,after_rows=rows)))
    snapshot={'all_erp_rows':[dict(item=mod.ITEM,code=p[1],daily='1' if bad=='daily' else p[4],custom=False) for p in mod.PAIRS]}
    a=Mock();a.resolve_snapshot.side_effect=lambda s:s
    t=SimpleNamespace(root=tmp_path,authority=a)
    scope=dict(sku_facts=[{'facts':f} for f in facts],page_evidence={'path':'original'})
    if bad:
        with pytest.raises(ValueError):mod.bind_export(t,scope,snapshot,tmp_path/'action')
        a.register_source.assert_not_called()
    else:
        before=copy.deepcopy(snapshot)
        assert mod.bind_export(t,scope,snapshot,tmp_path/'action')==before
        assert snapshot==before
        saved=json.loads((tmp_path/'cabinet-active.json').read_text(encoding='utf-8'))
        assert set(saved['active_skus'])=={p[2] for p in mod.PAIRS}|{'900','901','902'}
        assert len(saved['restored'])==2
        assert saved['database_write'] is False
        a.register_source.assert_called_once()


@pytest.mark.parametrize('case',['new','already','partial','multiple'])
def test_missing_reserves_use_exact_existing_offer_not_new_bulk(tmp_path,monkeypatch,case):
    import importlib.util
    import campaign_discount_include
    new=sorted(p[2] for p in mod.PAIRS)
    window=dict(start='2026-09-16 20:00:00',end='2026-09-27 23:59:59')
    offer=dict(offer_id='123',**window,items=[dict(item=mod.ITEM,status='success')],
               rows=[dict(item=mod.ITEM,sku=s) for s in (new if case=='already' else new[:1] if case=='partial' else [])])
    a=SimpleNamespace(discount_offers=lambda:[offer,offer] if case=='multiple' else [offer])
    calls=[]
    def job(step,payload,path):
        calls.append((step,payload))
        if step=='discount_item_discovery':return {'result':{'rows':[]}}
        return {'state':'finished','result':{}}
    t=SimpleNamespace(root=tmp_path,authority=a,job=job,identity=lambda p:{'shop_name':'畔色木作'})
    persist(tmp_path/'resolved-snapshot.json',{'resolved_price_version_sha256':'version'})
    def load_parser(module):module.analyze=lambda files,**kw:dict(price_window=kw)
    monkeypatch.setattr(importlib.util,'spec_from_file_location',lambda *args:SimpleNamespace(loader=SimpleNamespace(exec_module=load_parser)))
    monkeypatch.setattr(importlib.util,'module_from_spec',lambda spec:SimpleNamespace())
    prepare=Mock(return_value=dict(claim_id='c',rows=[dict(item=mod.ITEM,sku=s) for s in new]))
    record=Mock()
    monkeypatch.setattr(campaign_discount_include,'prepare',prepare)
    monkeypatch.setattr(campaign_discount_include,'record',record)
    payload={'time_binding':{'segment':dict(price_window=window,campaign=mod.AUTUMN,official_rate='.12')}}
    if case in ('partial','multiple'):
        with pytest.raises(ValueError):mod.include_missing(t,payload,tmp_path/'action')
        assert not calls
    else:
        mod.include_missing(t,payload,tmp_path/'action')
        if case=='already':assert not calls
        else:
            assert [s for s,p in calls]==['discount_item_discovery','discount_readback','discount_include']
            assert calls[1][1]['offers']==[dict(offer_id='123',item=mod.ITEM,sku_ids=new)]
            requested=prepare.call_args.args[1]
            assert requested['start']==window['start'] and requested['rate']=='.12'
            assert requested['price_version']=='version'
            record.assert_called_once()

import json
from copy import deepcopy
from pathlib import Path
import pytest
from campaign_catalog_alias_refresh import refresh
from campaign_entry_authority import file_sha

@pytest.mark.parametrize('change',[None,'sku','spec','code','custom'])
def test_refresh_revalidates_old_alias_against_current_physical_facts(tmp_path,change):
    registry=tmp_path/'registry.json';registry.write_text('[]',encoding='utf-8')
    alias=dict(item='1',sku='2',erp_code='PPS123456789',official_sku_code='123456789',repair_kind='verified_bound_legacy_prefix')
    parent=dict(schema='campaign_scoped_catalog_repair_v1',registry_path=str(registry),
                sources=[dict(path=str(registry),sha256=file_sha(registry))],restored=[alias])
    source=tmp_path/'parent.json';source.write_text(json.dumps(parent),encoding='utf-8')
    row=dict(item='1',sku='2',alt=[],code='PPS123456789',sku_name='颜色分类:灰;',custom=False,daily='100')
    fact=dict(item='1',sku='2',sku_code='123456789',attributes='颜色分类:灰;')
    if change=='sku':row['sku']='9'
    if change=='spec':row['sku_name']='颜色分类:白;'
    if change=='code':row['code']='PPS9'
    if change=='custom':row['custom']=True
    snapshot=dict(captured_at='current',resolved_price_version_sha256='version',all_erp_rows=[row])
    scope=dict(complete=True,sku_facts=[{'facts':fact}])
    class A:
        def sources(self):return [dict(kind='catalog',document=parent,path=str(source),sha256=file_sha(source))]
        def register_source(self,*args):self.registered=args
        def resolve_snapshot(self,value):return value
    authority=A();before=deepcopy(snapshot)
    if change:
        with pytest.raises(ValueError):refresh(authority,snapshot,scope,tmp_path/'out')
        assert not hasattr(authority,'registered')
    else:
        _,receipt=refresh(authority,snapshot,scope,tmp_path/'out')
        assert receipt['mapped_pairs']==[('1','2')]
        assert hasattr(authority,'registered')
    assert snapshot==before

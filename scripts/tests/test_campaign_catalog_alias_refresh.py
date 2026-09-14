import json
import sys
from pathlib import Path
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_catalog_alias_refresh import refresh
from campaign_entry_authority import file_sha


@pytest.mark.parametrize('fault',[None,'new_unapproved','changed_code','ambiguous','duplicate_fact','incomplete','changed_registry'])
def test_only_prior_exact_alias_current_scope_and_unchanged_prices(tmp_path,fault):
    registry=tmp_path/'registry.json'
    registry.write_text(json.dumps([dict(code='delisted',value_plain='[]')]))
    old=tmp_path/'old.json';old.write_text('{}')
    alias=dict(item='720234422814',sku='6135325229595',erp_code='PPS2316001040302',
               official_sku_code='PPS2316001040302|',repair_kind='verified_exact_trailing_delimiter')
    doc=dict(schema='campaign_scoped_catalog_repair_v1',restored=[alias],registry_path=str(registry),
             sources=[dict(path=str(registry),sha256=file_sha(registry))])
    class Authority:
        registered=[]
        def sources(self):
            return [] if fault=='new_unapproved' else [dict(kind='catalog',document=doc,path=str(old),sha256=file_sha(old))]
        def register_source(self,path,kind,sha): self.registered.append(dict(path=path,sha256=sha))
        def resolve_snapshot(self,snapshot): return dict(snapshot,catalog_repair_sources=list(self.registered))
    a=Authority();a.registered=[]
    snapshot=dict(captured_at='new-time',resolved_price_version_sha256='price-version',
                  all_erp_rows=[dict(code=alias['erp_code'],item=alias['item'],sku='old',alt=[],daily='4237.50',big_target='2744.90')])
    scope=dict(complete=True,sku_facts=[dict(facts=dict(item=alias['item'],sku=alias['sku'],sku_code=alias['official_sku_code']))])
    if fault=='changed_code':scope['sku_facts'][0]['facts']['sku_code']='OTHER'
    if fault=='ambiguous':snapshot['all_erp_rows']*=2
    if fault=='duplicate_fact':scope['sku_facts']*=2
    if fault=='incomplete':scope['complete']=False
    if fault=='changed_registry':registry.write_text('changed')
    original=deepcopy(snapshot)
    if fault in ('ambiguous','duplicate_fact','incomplete','changed_registry'):
        with pytest.raises(ValueError):refresh(a,snapshot,scope,tmp_path/'out')
        assert not a.registered
    else:
        result,receipt=refresh(a,snapshot,scope,tmp_path/'out')
        assert result['resolved_price_version_sha256']=='price-version'
        if fault in ('new_unapproved','changed_code'):
            assert receipt is None and not a.registered
        else:
            assert receipt['mapped_pairs']==[(alias['item'],alias['sku'])]
            from campaign_catalog_repair import mapped_rows
            assert alias['sku'] in mapped_rows(result,result['all_erp_rows'])[0]['alt']
    assert snapshot==original

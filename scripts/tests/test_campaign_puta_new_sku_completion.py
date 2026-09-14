from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_puta_new_sku_completion as p
from campaign_continuous_execute import validate_request


def test_exact_current_request_is_valid():
    assert validate_request(p.REQUEST)=='畔色木作'
    assert p.WINDOW=={'start':'2026-09-28 00:00:00','end':'2026-09-30 23:59:59'}


@pytest.mark.parametrize('change',[
    {'items':['793052650673']},{'items':['793202812082']},{'items':[p.ITEM,'793052650673']},
    {'existing_new_sku':'5193229462948'},{'price_window':{'start':'2026-09-16 20:00:00','end':'2026-09-27 23:59:59'}},
    {'parent_request_id':'f'*64},{'rule_sha':'f'*64},{'price':'1'},
])
def test_scope_cannot_expand_or_retry_autumn(change):
    with pytest.raises(ValueError):validate_request(dict(p.REQUEST,**change))


def test_no_whole_product_discount_upload_even_after_new_sku_include(tmp_path):
    transport=p.PutaTransport(None,None,root=tmp_path,request={},artifact_roots=[])
    with pytest.raises(ValueError,match='partial_discount'):
        transport.execute('discount','a'*64,{'items':[p.ITEM],'rule_sha':p.RULE_SHA})
    with pytest.raises(ValueError,match='scope_expanded'):
        transport.execute('signup','b'*64,{'items':['793052650673'],'rule_sha':p.RULE_SHA})


def test_current_scope_keeps_full_export_and_previous_failure(tmp_path):
    import sqlite3
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE attempts(id,item,campaign,phase,status,evidence)')
    db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',('ce7be145a079482badeee181010b3792:'+p.ITEM,p.ITEM,p.CAMPAIGN,'signup','failed','{}'))
    authority=SimpleNamespace(db=db,blocked=lambda *args:{})
    transport=p.PutaTransport(None,authority,root=tmp_path,request={},artifact_roots=[])
    scope={'complete':True,'sku_facts':[{'facts':{'item':p.ITEM,'sku':p.NEW}}],
           'observed_item_count':59,'page_count':3,'platform_rows':[{'item':p.ITEM,'on_sale':True}]}
    p.persist(tmp_path/'product-scope.json',scope)
    p.persist(tmp_path/'resolved-snapshot.json',{'resolved_price_version_sha256':'a'*64})
    value=transport.step_scope('b'*64,{'identity':{'start':'a','end':'b'}},tmp_path)
    assert value['observed_item_count']==59 and value['page_count']==3
    assert value['erp_sellable']==[p.ITEM] and value['changed_existing_sku_scope'] is True
    assert value['previous_failed_attempt'].endswith(':'+p.ITEM)
    assert Path(value['prior_outcomes_evidence']).is_file()
    authority.blocked=lambda *args:{p.ITEM:'unknown'}
    with pytest.raises(ValueError,match='not_success_or_unknown'):
        transport.step_scope('b'*64,{'identity':{'start':'a','end':'b'}},tmp_path)
    db.close()

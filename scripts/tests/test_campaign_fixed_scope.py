from copy import deepcopy
import sys
from pathlib import Path
from unittest.mock import Mock,patch

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_product_scope import template_scope_issues
from campaign_continuous_transport import CampaignTransport,persist
from campaign_entry_authority import file_sha


def scope():
    return {'complete':True,'page_evidence':{'sha256':'actual-export'},'sku_facts':[
        {'facts':{'item':'1','sku':'11','stock':'0'}},
        {'facts':{'item':'1','sku':'12','stock':'999'}},
        {'facts':{'item':'2','sku':'21','stock':'1000'}}]}


def rows():
    return [{'item':'1','sku':'11'},{'item':'1','sku':'12'},{'item':'2','sku':'21'}]


def test_same_scope_and_zero_stock_is_not_excluded():
    assert template_scope_issues(scope(),rows(),['1','2'])==[]
    assert template_scope_issues(scope(),rows()[1:],['1'])[0]['sku']=='11'


def test_new_sku_only_marks_its_product_and_does_not_infer_rotation():
    issues=template_scope_issues(scope(),rows()[:1]+rows()[2:],['1','2'])
    assert len(issues)==1 and issues[0]['item']=='1' and issues[0]['sku']=='12'
    assert issues[0]['error']=='current_sku_missing_from_fixed_template'
    assert issues[0]['enabled_state']=='unknown' and not issues[0]['requires_rotation']


def test_removed_sku_is_not_silently_uploaded():
    issues=template_scope_issues(scope(),rows()+[{'item':'2','sku':'22'}],['1','2'])
    assert [(x['item'],x['sku'],x['error']) for x in issues]==[
        ('2','22','fixed_template_sku_missing_from_current_export')]


def test_filtered_retry_ignores_other_product_mismatch():
    assert template_scope_issues(scope(),rows()[:1]+rows()[2:],['2'])==[]


def test_new_product_missing_template_lists_all_skus():
    assert {x['sku'] for x in template_scope_issues(scope(),rows()[2:],['1'])}=={'11','12'}


def test_incomplete_and_duplicate_facts_rejected():
    value=scope();value['complete']=False
    with pytest.raises(ValueError,match='complete'):template_scope_issues(value,rows(),['1'])
    value=scope();value['sku_facts'].append(deepcopy(value['sku_facts'][0]))
    with pytest.raises(ValueError,match='duplicate'):template_scope_issues(value,rows(),['1'])
    with pytest.raises(ValueError,match='duplicate'):template_scope_issues(scope(),rows()+rows()[:1],['1'])


def test_transport_does_not_generate_files_or_contact_browser_on_scope_issue(tmp_path):
    source=tmp_path/'master.xlsx';source.write_bytes(b'local-fixture-only')
    persist(tmp_path/'product-scope.json',scope())
    edge=Mock();authority=Mock()
    transport=CampaignTransport(edge,authority,root=tmp_path,request={},artifact_roots=[tmp_path])
    payload={'items':['1','2'],'template':{'fixed_master':True,'path':str(source),'sha256':file_sha(source)}}
    with patch('campaign_official_template.template_rows',return_value=rows()[:1]+rows()[2:]),patch(
            'campaign_generate_current_files._generate') as generate:
        result=transport.step_generate('one',payload,tmp_path/'action')
    assert result['input_issues'][0]['sku']=='12'
    generate.assert_not_called();assert not edge.mock_calls and not authority.mock_calls
    assert Path(result['source_evidence']).is_file()

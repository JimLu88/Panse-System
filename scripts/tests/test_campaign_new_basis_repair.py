import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_continuous_repairs import execute_repairs


@pytest.mark.parametrize('approved',[True,False])
def test_new_basis_is_reclassified_before_exact_repair_assertion(tmp_path,approved):
    reports=tmp_path/'reports';reports.mkdir();folder=tmp_path/'actions'/'a';folder.mkdir(parents=True)
    old={'bundle_id':'b','source_terminal':'official.json',
         'errors':[{'parse_issue':'fixed_original_evidence_missing'}]}
    (reports/'123.json').write_text(json.dumps(old))
    decisions={'i':[{'sku':'s','repair':{'kind':'custom_price','price':'396.99'}}]}
    revised=dict(old,errors=[{'approved':approved}])
    db=sqlite3.connect(':memory:')
    a=SimpleNamespace(db=db,get_bundle=lambda b:{'campaign':'c'})
    transport=SimpleNamespace(authority=a,root=tmp_path)
    payload={'failed_batch':'123','decisions':decisions,'items':['i']}
    def classify(errors):return (decisions if errors==revised['errors'] and approved else {},{})
    with patch('campaign_continuous_repairs.mapping_report',side_effect=lambda r,p:r),\
         patch('campaign_price_report_recovery.reclassify',return_value=revised) as refresh,\
         patch('campaign_continuous_repairs.classify_items',side_effect=classify):
        if approved:
            result=execute_repairs(transport,'a',payload,folder)
            assert result['items']==[{'item':'i','outcome':'success','changed':True}]
            assert (folder/'custom-authorization.json').exists()
        else:
            with pytest.raises(ValueError,match='decisions_do_not_match'):execute_repairs(transport,'a',payload,folder)
            assert not (folder/'custom-authorization.json').exists()
        refresh.assert_called_once()
    db.close()

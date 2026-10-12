import sqlite3
from types import SimpleNamespace
import pytest
import campaign_continuous_repairs as repairs
import campaign_scoped_failure_report
import campaign_price_report_recovery


@pytest.mark.parametrize('bad_item,should_call',[('outside',False),('selected',True)])
def test_only_selected_repair_issues_trigger_reparse(tmp_path,monkeypatch,bad_item,should_call):
    decisions={'selected':[dict(item='selected',sku='s',action='repair',repair=dict(kind='file_price'))]}
    payload=dict(items=['selected'],decisions=decisions,failed_batch='b',failed_phase='signup')
    report=dict(bundle_id='bundle',errors=[dict(item=bad_item,parse_issue='unparsed_or_incomplete_official_failure')])
    monkeypatch.setattr(campaign_scoped_failure_report,'read',lambda *a:(tmp_path/'report.json',report))
    monkeypatch.setattr(repairs,'mapping_report',lambda r,p:r)
    monkeypatch.setattr(repairs,'classify_items',lambda e:(decisions,{}))
    def blocked(*a):raise RuntimeError('selected-reparse')
    monkeypatch.setattr(campaign_price_report_recovery,'reclassify',blocked)
    authority=SimpleNamespace(db=sqlite3.connect(':memory:'),get_bundle=lambda b:{})
    transport=SimpleNamespace(authority=authority,root=tmp_path)
    if should_call:
        with pytest.raises(RuntimeError,match='selected-reparse'):repairs.execute_repairs(transport,'a',payload,tmp_path)
    else:
        r=repairs.execute_repairs(transport,'a',payload,tmp_path)
        assert r['items']==[dict(item='selected',outcome='success',changed=True)]

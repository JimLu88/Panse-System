import hashlib
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_continuous_repairs import mapping_report
from campaign_continuous_policy import classify_items


def test_verified_export_augments_old_report_without_overwriting_it(tmp_path):
    e=dict(item='1',sku='2',kind='mapping',terminal='failed',batch='b',message='invalid',official_evidence='official',official_invalid_or_disabled=True)
    doc=dict(rule='failure-remediation-20260912',scope={'complete':True},excluded=[{'item':'1','sku':'2'}],errors=[e],batch='b')
    path=tmp_path/'scope.json';path.write_text(json.dumps(doc))
    ref={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    target=dict(e,full_official_export_verified=True,remove_from_signup=True,mapping_scope_evidence=ref)
    decisions,_=classify_items([target]);payload=dict(items=['1'],decisions=decisions,failed_batch='b')
    old=dict(errors=[dict(e,kind='unknown',parse_issue='mapping_export_unavailable:failed')],bundle_id='bundle')
    result=mapping_report(old,payload)
    assert result['errors']==[target] and old['errors'][0]['kind']=='unknown'
    assert classify_items(result['errors'])[0]==decisions
    old['errors'][0]['message']='different original failure'
    with pytest.raises(ValueError,match='source_changed'):mapping_report(old,payload)
    path.write_text('{}')
    with pytest.raises(ValueError,match='evidence_changed'):mapping_report(old,payload)

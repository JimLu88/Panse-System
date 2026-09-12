from copy import deepcopy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from campaign_catalog_repair import excluded_pairs,mapped_rows
from campaign_entry_authority import file_sha
from campaign_price_snapshot import SnapshotRows,build_snapshot

def test_snapshot_registry_does_not_change_price_fingerprint():
    rows=SnapshotRows([dict(code='E',daily='10',listing_status='在售',item='1',sku='2')])
    rows.registered_delisted_sku_ids=['3']
    new=build_snapshot(rows);old=build_snapshot(list(rows))
    assert new['resolved_price_version_sha256']==old['resolved_price_version_sha256']
    assert new['registered_delisted_sku_ids']==['3']
    assert excluded_pairs(new,[dict(item='1',sku='3'),dict(item='1',sku='2',stock='0')])=={('1','3')}

def fixture(tmp_path):
    def write(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value),encoding='utf-8');return str(path)
    registry=write('registry.json',[dict(code='delisted',value_plain='["3"]')])
    scope=write('scope.json',dict(scope=dict(complete=True,sku_facts=[dict(facts=dict(item='1',sku='4',sku_code='E|'))])))
    doc=dict(schema='campaign_scoped_catalog_repair_v1',snapshot_captured_at='now',price_version='v',
        registry_path=registry,official_scope_path=scope,
        sources=[dict(path=p,sha256=file_sha(p)) for p in (registry,scope)],
        retired=[dict(item='1',sku='3')],restored=[dict(item='1',sku='4',erp_code='E',official_sku_code='E|',repair_kind='verified_exact_trailing_delimiter')])
    path=write('receipt.json',doc)
    snap=dict(captured_at='now',resolved_price_version_sha256='v',catalog_repair_sources=[dict(path=path,sha256=file_sha(path))])
    return snap,doc,write

def test_scoped_typo_does_not_mutate_prices_or_original_ids(tmp_path):
    snapshot,doc,write=fixture(tmp_path)
    rows=[dict(item='1',sku='2',code='E',daily='123.45')];before=deepcopy(rows)
    assert mapped_rows(snapshot,rows)[0]['alt']==['4']
    assert rows==before
    assert excluded_pairs(snapshot,[dict(item='1',sku='3')])=={('1','3')}
    snapshot['captured_at']='next-run'
    with pytest.raises(ValueError,match='wrong_snapshot'):mapped_rows(snapshot,rows)

def test_changed_or_invented_retirement_is_rejected(tmp_path):
    snapshot,doc,write=fixture(tmp_path)
    doc['retired'].append(dict(item='1',sku='999'))
    path=write('other.json',doc);snapshot['catalog_repair_sources']=[dict(path=path,sha256=file_sha(path))]
    with pytest.raises(ValueError,match='not_registered'):excluded_pairs(snapshot,[])

def test_existing_official_exclusion_empty_is_still_valid():
    from campaign_failure_remediation import excluded_pairs as official_excluded
    assert official_excluded([])==set()


@pytest.mark.parametrize('change',['none','sku','spec','code','custom'])
def test_legacy_prefix_requires_existing_exact_identity_and_spec(tmp_path,change):
    snapshot,doc,write=fixture(tmp_path)
    scope=write('legacy-scope.json',{'scope':{'complete':True,'sku_facts':[{'facts':dict(item='1',sku='2',sku_code='12345',attributes='oak-large')} ]}})
    doc.update(official_scope_path=scope,restored=[dict(item='1',sku='2',erp_code='PPS12345',official_sku_code='12345',repair_kind='verified_bound_legacy_prefix')])
    doc['sources'].append(dict(path=scope,sha256=file_sha(scope)))
    path=write('legacy.json',doc);snapshot['catalog_repair_sources']=[dict(path=path,sha256=file_sha(path))]
    row=dict(item='1',sku='2',code='PPS12345',sku_name='oak-large',custom=False,daily='55')
    if change=='sku':row['sku']='9'
    if change=='spec':row['sku_name']='pine-small'
    if change=='code':row['code']='PPS54321'
    if change=='custom':row['custom']=True
    if change=='none':assert mapped_rows(snapshot,[row])==[row]
    else:
        with pytest.raises(ValueError):mapped_rows(snapshot,[row])


def test_remaining_summary_does_not_rewrite_historical_exceptions():
    from campaign_catalog_repair import remaining_mapping_summary
    snapshot={'registered_delisted_sku_ids':['2'],'all_erp_rows':[]}
    exceptions={'1':[dict(reason='erp_mapping_missing_or_not_unique',sku='2'),dict(reason='erp_mapping_missing_or_not_unique',sku='3')]}
    before=deepcopy(exceptions);summary=remaining_mapping_summary(snapshot,exceptions)
    assert summary['historical_sku_count']==2 and summary['remaining_sku_count']==1
    assert summary['products'][0]['remaining_skus']==['3'] and exceptions==before

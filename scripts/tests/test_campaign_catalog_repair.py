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

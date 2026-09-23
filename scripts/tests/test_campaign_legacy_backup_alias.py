"""Old signed alias receipts must resolve to an exact official export row."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_failure_remediation as aliases
import campaign_product_scope
from campaign_entry_authority import file_sha


ROW={'item':'100','sku':'200','erp_code':'ERP200','official_sku_code':'ERP200B1'}
SNAPSHOT={'all_erp_rows':[{'code':'ERP200','item':'100','sku':'200','alt':[]}]}


def write(path,body):
    path.write_text(json.dumps(body),encoding='utf-8')
    return {'path':str(path),'sha256':file_sha(path)}


def fixture(tmp_path,monkeypatch,kind):
    raw=tmp_path/'page-1-record-1.xlsx'
    raw.write_bytes(b'official export fixture')
    sha=file_sha(raw)
    monkeypatch.setattr(campaign_product_scope,'parse_export',lambda contents:
                        [dict(item='100',sku='200',sku_code='ERP200B1')]
                        if contents==raw.read_bytes() else [])
    export={'state':'downloaded','observed_item_ids':['100'],'observed_total':1,
            'page_count':1,'files':[{'path':str(raw),'sha256':sha,
                                    'scope':{'item_ids':['100'],'on_sale':True}}]}
    if kind=='export':
        ref=write(tmp_path/'product-export.json',export)
        return {'official_export_evidence':ref},raw
    write(tmp_path/'result.json',export)
    scope={'complete':True,'observed_item_count':1,'platform_rows':[{'item':'100'}],
           'source_sha256':sha,'sku_facts':[{'facts':{'item':'100','sku':'200',
             'sku_code':'ERP200B1'},'sources':[{'sha256':sha}]}]}
    ref=write(tmp_path/'scope.json',scope)
    return {'source':ref['path'],'source_sha256':ref['sha256']},raw


@pytest.mark.parametrize('kind',['export','scope'])
def test_legacy_document_level_provenance_is_exact(tmp_path,monkeypatch,kind):
    document,_=fixture(tmp_path,monkeypatch,kind)
    aliases._verified_legacy_backup_alias(SNAPSHOT,document,ROW)
    with pytest.raises(ValueError,match='backup_alias_not_in_official_export|backup_alias_official_scope_incomplete'):
        aliases._verified_legacy_backup_alias(SNAPSHOT,document,dict(ROW,official_sku_code='ERP200B2'))
    with pytest.raises(ValueError,match='backup_alias_erp_binding_not_unique'):
        aliases._verified_legacy_backup_alias({'all_erp_rows':[]},document,ROW)


@pytest.mark.parametrize('kind',['export','scope'])
def test_changed_official_bytes_or_missing_reference_rejected(tmp_path,monkeypatch,kind):
    document,raw=fixture(tmp_path,monkeypatch,kind)
    raw.write_bytes(b'changed')
    with pytest.raises(ValueError,match='backup_alias_source_changed'):
        aliases._verified_legacy_backup_alias(SNAPSHOT,document,ROW)
    with pytest.raises(ValueError,match='backup_alias_provenance_missing'):
        aliases._verified_legacy_backup_alias(SNAPSHOT,{},ROW)

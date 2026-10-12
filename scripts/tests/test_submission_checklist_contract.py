import copy
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
import campaign_submission_checklist_contract as c
from campaign_price_snapshot import digest
from campaign_entry_authority import file_sha


def setup_case(tmp_path, monkeypatch, custom=False):
    import campaign_official_template as official
    monkeypatch.setattr(official,'template_rows',lambda raw:[])
    path=tmp_path/'template.xlsx';path.write_bytes(b'template-test-double')
    proof=tmp_path/'export.json';proof.write_text(json.dumps({'shop_name':'测试店'}),encoding='utf-8')
    scope={'sku_facts':[{'facts':dict(item='100',sku='900',sku_code='PPS1',stock='1',attributes='榉木;整装')}],
           'page_evidence':{'path':str(proof),'sha256':file_sha(proof)}}
    monkeypatch.setattr(c,'read_scope',lambda ref:copy.deepcopy(scope))
    snapshot={'all_erp_rows':[dict(code='PPS1',item='100',sku='900',daily='100',medium_target='80',custom=custom)]}
    row=dict(item='100',sku='900',erp_code='PPS1',activity_price='100',custom=custom)
    if not custom:row['target']='80'
    body=dict(campaign='1/2/3',start='2026-10-13 00:00:00',end='2026-10-16 23:59:59',
        signup_rows=[row],planned_discount_rows=[dict(item='100',sku='900',final='80')],
        discount_reuse=[],official_rate='0.10',target='medium',template_path=str(path),template_sha256=file_sha(path),
        snapshot_path='snapshot.json',snapshot_sha256='snapshot-hash',price_version=digest(snapshot['all_erp_rows']),
        checklist_scope_source={'path':'scope.json','sha256':'scope-hash'})
    return body,snapshot,scope


def test_generation_and_verification_same_evidence(tmp_path,monkeypatch):
    body,snapshot,_=setup_case(tmp_path,monkeypatch)
    contract,issues=c.materialize(body,snapshot,{})
    assert not issues and contract['rows'][0]['final_price']=='80'
    body['submission_checklist']=contract
    assert c.verify_contract(body,snapshot,{})==contract
    body['submission_checklist']['rows'][0]['daily_price']='99'
    with pytest.raises(ValueError,match='source_binding_changed'):c.verify_contract(body,snapshot,{})


@pytest.mark.parametrize('price,expected',[('20',False),('19.99',True),('90',False)])
def test_custom_first_baseline_floor(tmp_path,monkeypatch,price,expected):
    body,snapshot,_=setup_case(tmp_path,monkeypatch,custom=True)
    body['signup_rows'][0]['activity_price']=price
    contract,issues=c.materialize(body,snapshot,{('100','900'):{'original':'100'}})
    assert bool(issues)==expected
    if not issues:assert contract['rows'][0]['original_price']=='100'


@pytest.mark.parametrize('final,expected',[('78',False),('82',False),('77.99',True),('82.01',True)])
def test_ordinary_fixed_target_not_previous_final(tmp_path,monkeypatch,final,expected):
    body,snapshot,_=setup_case(tmp_path,monkeypatch)
    body['planned_discount_rows'][0]['final']=final
    _,issues=c.materialize(body,snapshot,{})
    assert bool(issues)==expected


def test_missing_baseline_is_scoped_not_rotation(tmp_path,monkeypatch):
    body,snapshot,_=setup_case(tmp_path,monkeypatch,custom=True)
    _,issues=c.materialize(body,snapshot,{})
    assert issues==[dict(item='100',sku='900',error='checklist_fixed_original_missing_or_uncertain')]


def test_export_extra_sku_not_silently_omitted(tmp_path,monkeypatch):
    body,snapshot,scope=setup_case(tmp_path,monkeypatch)
    scope['sku_facts'].append({'facts':dict(item='100',sku='901',sku_code='PPS2',stock='1',attributes='窄柜')})
    _,issues=c.materialize(body,snapshot,{})
    assert issues==[dict(item='100',sku='901',error='checklist_current_sku_scope_mismatch')]


def test_reused_discount_final_has_priority(tmp_path,monkeypatch):
    body,snapshot,_=setup_case(tmp_path,monkeypatch)
    body['discount_reuse']=[dict(item='100',sku='900',final='77')]
    _,issues=c.materialize(body,snapshot,{})
    assert issues[0]['error']=='checklist_final_outside_fixed_target'


def test_price_snapshot_change_rejected(tmp_path,monkeypatch):
    body,snapshot,_=setup_case(tmp_path,monkeypatch)
    snapshot['all_erp_rows'][0]['daily']='99'
    with pytest.raises(ValueError,match='price_version_changed'):c.materialize(body,snapshot,{})


def test_scope_original_receipt_hash_is_checked(tmp_path):
    proof=tmp_path/'proof.json';proof.write_text('{}')
    scope=tmp_path/'scope.json';scope.write_text(json.dumps({'page_evidence':{'path':str(proof),'sha256':'changed'}}))
    with pytest.raises(ValueError,match='original_export_receipt_changed'):
        c.read_scope({'path':str(scope),'sha256':file_sha(scope)})

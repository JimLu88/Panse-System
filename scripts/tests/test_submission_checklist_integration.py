"""Synthetic full generator/claim/resume tests; no live Authority or browser."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from campaign_entry_authority import Authority, file_sha
from campaign_price_snapshot import build_snapshot
from campaign_product_scope import complete_scope
from campaign_generate_current_files import _generate
from campaign_submission_gate import validated_body, verify_claim, consume_claim
from campaign_artifact_recovery import ensure_generated
from test_campaign_current_rate import rate_fixture


@pytest.fixture(autouse=True)
def isolated_evidence_roots(tmp_path,monkeypatch):
    import campaign_three_discount_overlay
    monkeypatch.setattr(campaign_three_discount_overlay,'ROOT',tmp_path/'empty-overlays')
    monkeypatch.setenv('PANSE_SKU_FACT_ROOT',str(tmp_path/'empty-facts'))


def prepare(tmp_path):
    import openpyxl
    def write(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');return p
    item='917179577721'
    rows=[dict(item=item,sku=str(6241018727157+n),code='NORMAL'+str(n),daily='100',
               medium_target='88',big_target='88',custom=False,alt=[]) for n in range(4)]
    wb=openpyxl.Workbook();ws=wb.active;ws.title='发布模板'
    ws.append(['商品Id','skuId','商家编码','价格(元)','库存(件)','销售属性'])
    for r in rows:ws.append([item,r['sku'],r['code'],'100','2','榉木;整装'])
    export=tmp_path/'export.xlsx';wb.save(export)
    data=dict(shop_name='测试店',observed_item_ids=[item],observed_total=1,page_count=1,
              files=[dict(path=str(export),sha256=file_sha(export))])
    proof=write('export.json',data)
    scope=complete_scope([(export.read_bytes(),file_sha(export))],observed_item_ids=[item],
        observed_page_count=1,observed_total=1,page_evidence=dict(path=str(proof),sha256=file_sha(proof)))
    scope_path=write('scope.json',scope)
    template=tmp_path/'template.xlsx';template.write_bytes(rate_fixture('12%'))
    args=SimpleNamespace(snapshot=write('snapshot.json',build_snapshot(rows)),activity_template=template,
        checklist_scope_source=scope_path,output_dir=tmp_path/'result',start='2026-10-13 00:00:00',
        end='2026-10-16 23:59:59',campaign_key='1/2/3',official_rate='12%',target='big',
        signup_items=None,discount_items=None,custom_basis_receipt=[],custom_corrections=None)
    auth=Authority(tmp_path/'test-only.sqlite3',write('sources.json',{'sources':[]}))
    return args,auth


def test_full_generation_claim_consumption_unknown_protection_and_resume(tmp_path):
    args,auth=prepare(tmp_path)
    try:
        result=ensure_generated(args,auth,generator=_generate)
        assert not result['issues']
        bundle=result['entry_bundle_id'];body,file=validated_body(auth,bundle,'signup')
        assert len(body['submission_checklist']['rows'])==4
        before={p.name:p.read_bytes() for p in args.output_dir.iterdir()}
        assert ensure_generated(args,auth,generator=lambda *a:pytest.fail('must not regenerate'))==result
        assert before=={p.name:p.read_bytes() for p in args.output_dir.iterdir()}
        claim=auth.claim(bundle,'signup',transport='dedicated_edge_v1')
        verified=verify_claim(auth,claim)
        assert verified['submission_checklist']==body['submission_checklist']
        assert len(verified['sku_rows'])==4 and verified['shop']=='测试店'
        consumed=consume_claim(auth,claim,'a'*64)
        assert consumed['dispatch_consumed']
        with pytest.raises(ValueError):consume_claim(auth,claim,'b'*64)
        with pytest.raises(ValueError,match='must_not_replay'):auth.claim(bundle,'signup',transport='dedicated_edge_v1')
        # Read-only original dispatched receipt checks remain usable; no new gate
        # is imposed on recovering an already performed platform effect.
        assert verify_claim(auth,claim,dispatched_job='a'*64)['verified_claim']
        receipt=dict(schema='campaign_entry_terminal_v1',claim_id=claim,campaign=body['campaign'],
            phase='signup',start=body['start'],end=body['end'],file_sha256=file['sha256'],
            batch_id='TEST-ONLY',terminal=True,items=[dict(item='917179577721',status='success')])
        proof=tmp_path/'official-test-only.json';proof.write_text(json.dumps(receipt))
        auth.terminal(claim,dict(receipt,evidence_path=str(proof)))
        receipt['items'][0]['status']='failed'
        stale=tmp_path/'old-same-name-test-only.json';stale.write_text(json.dumps(receipt))
        with pytest.raises(ValueError,match='terminal_conflict'):
            auth.terminal(claim,dict(receipt,evidence_path=str(stale)))
        assert auth.blocked(body['campaign'],'signup',body['start'],body['end'])['917179577721']=='success'
    finally:auth.close()


def test_resume_changed_scope_never_regenerates(tmp_path):
    args,auth=prepare(tmp_path)
    try:
        ensure_generated(args,auth,generator=_generate)
        args.checklist_scope_source.write_text('{}')
        with pytest.raises(ValueError,match='artifact_context_changed'):
            ensure_generated(args,auth,generator=lambda *a:pytest.fail('must not regenerate'))
        assert auth.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==0
    finally:auth.close()


def test_missing_producer_evidence_does_not_create_claim_or_upload_file(tmp_path):
    args,auth=prepare(tmp_path);args.checklist_scope_source=None
    try:
        with pytest.raises(ValueError,match='scope_source_missing'):_generate(args,auth)
        assert not args.output_dir.exists()
        assert auth.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==0
    finally:auth.close()

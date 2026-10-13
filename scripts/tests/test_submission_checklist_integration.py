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


@pytest.mark.parametrize('old_end,historical',[
    ('2026-09-30 23:59:59',True),('2026-10-14 00:00:00',False)])
def test_transport_with_prior_keeps_owner_02_segment_fix(tmp_path,old_end,historical):
    import sqlite3
    from campaign_continuous_transport import CampaignTransport
    campaign='legacy/itemApply/3172207691'
    proof=tmp_path/'terminal.json'
    proof.write_text(json.dumps(dict(phase='signup',terminal=True,campaign=campaign,
        claim_id='claim',batch_id='123',items=[dict(item='1',status='failed')])),encoding='utf-8')
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE attempts(item,status,bundle_id,id,evidence,campaign,phase)')
    db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',('1','failed','bundle','claim:1',
        json.dumps(dict(path=str(proof),sha256=file_sha(proof),batch='123')),campaign,'signup'))
    authority=SimpleNamespace(db=db,get_bundle=lambda _:dict(campaign=campaign,
        start='2026-09-28 00:00:00',end=old_end))
    payload=dict(identity=dict(campaign_id='legacy',phase_id='itemApply',sign_record_id='3172207691'),
        time_binding=dict(segment=dict(kind='daily',campaign=campaign,price_window=dict(
            start='2026-10-13 00:00:00',end='2026-10-16 23:59:59'))))
    authority.blocked=lambda *args:{'2':'success','3':'unknown'}
    payload['identity'].update(start='2026-10-13 00:00:00',end='2026-10-16 23:59:59')
    transport=object.__new__(CampaignTransport)
    transport.authority=authority;transport.request={}
    before=[tuple(r) for r in authority.db.execute('SELECT * FROM attempts')]
    try:
        result=transport.with_prior({'complete':True},payload,tmp_path/'scope-action')
        expected={'2':'success','3':'unknown'}
        if not historical:expected['1']='failed'
        assert result['prior_outcomes']==expected
        proof=json.loads(Path(result['prior_outcomes_evidence']).read_text())
        assert bool(proof['historical_segment_failures'])==historical
        assert [tuple(r) for r in authority.db.execute('SELECT * FROM attempts')]==before
    finally:authority.db.close()


def legacy_bundle(args,auth):
    result=_generate(args,auth)
    body=auth.get_bundle(result['entry_bundle_id'])
    body.pop('submission_checklist');body.pop('checklist_scope_source')
    return auth.save_bundle(body)


def legacy_checkpoint(args,auth):
    from campaign_artifact_recovery import input_context
    bundle=legacy_bundle(args,auth)
    receipt=args.output_dir/'receipt.json'
    result=json.loads(receipt.read_text(encoding='utf-8'))
    result['entry_bundle_id']=bundle
    receipt.write_text(json.dumps(result),encoding='utf-8')
    context=input_context(args);context.pop('checklist_scope_source')
    path=args.output_dir.parent/(args.output_dir.name+'-input-context.json')
    path.write_text(json.dumps(context),encoding='utf-8')
    return bundle,path


def test_original_controller_context_additive_migration_is_idempotent(tmp_path):
    args,auth=prepare(tmp_path)
    try:
        bundle,context=legacy_checkpoint(args,auth)
        original=context.read_bytes()
        files={p.name:p.read_bytes() for p in args.output_dir.iterdir()}
        claim=auth.claim(bundle,'signup',transport='dedicated_edge_v1')
        attempts=[tuple(r) for r in auth.db.execute('SELECT * FROM attempts')]
        for _ in range(2):
            result=ensure_generated(args,auth,generator=lambda *a:pytest.fail('regeneration'))
            assert result['entry_bundle_id']==bundle
            assert context.read_bytes()==original
            assert files=={p.name:p.read_bytes() for p in args.output_dir.iterdir()}
            assert attempts==[tuple(r) for r in auth.db.execute('SELECT * FROM attempts')]
        assert verify_claim(auth,claim)['submission_checklist']
        args.end='2026-10-17 23:59:59'
        with pytest.raises(ValueError,match='artifact_context_changed'):ensure_generated(args,auth)
    finally:auth.close()


def test_original_controller_migration_cannot_adopt_dispatched_effect(tmp_path):
    args,auth=prepare(tmp_path)
    try:
        bundle,context=legacy_checkpoint(args,auth)
        original=context.read_bytes()
        claim=auth.claim(bundle,'signup',transport='dedicated_edge_v1')
        auth.db.execute("UPDATE claim_transports SET state='dispatched' WHERE claim_id=?",(claim,))
        auth.db.commit()
        with pytest.raises(ValueError,match='original_readonly_recovery'):ensure_generated(args,auth)
        assert context.read_bytes()==original
        from campaign_checklist_adoption import receipt_path
        assert not receipt_path(auth,bundle).exists()
    finally:auth.close()


def test_legacy_adoption_preserves_original_files_bundle_and_claim(tmp_path):
    from campaign_checklist_adoption import adopt_existing
    args,auth=prepare(tmp_path)
    try:
        bundle=legacy_bundle(args,auth);original=auth.get_bundle(bundle)
        before={p.name:p.read_bytes() for p in args.output_dir.iterdir()}
        with pytest.raises(ValueError,match='adoption_required'):validated_body(auth,bundle,'signup')
        claim=auth.claim(bundle,'signup',transport='dedicated_edge_v1')
        receipt=adopt_existing(auth,bundle,args.checklist_scope_source)
        assert adopt_existing(auth,bundle,args.checklist_scope_source)==receipt
        assert auth.get_bundle(bundle)==original
        assert before=={p.name:p.read_bytes() for p in args.output_dir.iterdir()}
        assert verify_claim(auth,claim)['submission_checklist']==receipt['submission_checklist']
        consume_claim(auth,claim,'b'*64)
        with pytest.raises(ValueError,match='original_readonly_recovery'):
            adopt_existing(auth,bundle,args.checklist_scope_source)
        assert verify_claim(auth,claim,dispatched_job='b'*64)['verified_claim']
    finally:auth.close()


def test_adoption_source_change_cannot_release_old_check(tmp_path):
    from campaign_checklist_adoption import adopt_existing
    args,auth=prepare(tmp_path)
    try:
        bundle=legacy_bundle(args,auth)
        adopt_existing(auth,bundle,args.checklist_scope_source)
        args.checklist_scope_source.write_text('{}')
        with pytest.raises(ValueError,match='source_missing_or_changed'):validated_body(auth,bundle,'signup')
        assert auth.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==0
    finally:auth.close()


def test_fresh_process_owner_reuses_checkpoint_and_blocks_consumed_claim(tmp_path):
    args,auth=prepare(tmp_path)
    result=ensure_generated(args,auth)
    bundle=result['entry_bundle_id'];claim=auth.claim(bundle,'signup',transport='dedicated_edge_v1')
    dbpath,manifest=auth.path,auth.manifest
    original={p.name:p.read_bytes() for p in args.output_dir.iterdir()}
    auth.close()
    restored=Authority(dbpath,manifest)
    try:
        assert ensure_generated(args,restored,generator=lambda *a:pytest.fail('restart regenerated'))==result
        assert verify_claim(restored,claim)['bundle_id']==bundle
        consume_claim(restored,claim,'e'*64)
    finally:restored.close()
    restored=Authority(dbpath,manifest)
    try:
        with pytest.raises(ValueError,match='do_replay|do_not_replay|already_dispatched'):
            verify_claim(restored,claim)
        assert original=={p.name:p.read_bytes() for p in args.output_dir.iterdir()}
        assert verify_claim(restored,claim,dispatched_job='e'*64)['verified_claim']
    finally:restored.close()


def test_single_sku_retry_cannot_drop_siblings_even_with_updated_bundle(tmp_path):
    args,auth=prepare(tmp_path)
    try:
        result=ensure_generated(args,auth);body=auth.get_bundle(result['entry_bundle_id'])
        body['signup_rows']=body['signup_rows'][:1]
        from campaign_submission_checklist_contract import materialize
        snapshot=auth.resolve_snapshot(json.loads(args.snapshot.read_text(encoding='utf-8')))
        _,issues=materialize(body,snapshot,auth.bases(snapshot))
        assert sum(i['error']=='checklist_current_sku_scope_mismatch' for i in issues)==3
        changed=auth.save_bundle(body)
        with pytest.raises(ValueError,match='submission_checklist_inputs_invalid'):
            validated_body(auth,changed,'signup')
        assert auth.db.execute('SELECT count(*) FROM attempts').fetchone()[0]==0
    finally:auth.close()


def test_owner02_enriched_scope_preserves_every_original_export_fact(tmp_path):
    from campaign_submission_checklist_contract import validate_scope
    args,auth=prepare(tmp_path)
    try:
        scope=json.loads(args.checklist_scope_source.read_text(encoding='utf-8'))
        scope.update(erp_mapping={'extra':'controller metadata'},prior_outcomes={'older':'success'})
        assert validate_scope(scope)==scope
        scope['sku_facts'][0]['facts']['attributes']='tampered'
        with pytest.raises(ValueError,match='scope_not_original_export'):validate_scope(scope)
    finally:auth.close()

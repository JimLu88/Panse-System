from datetime import datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import pytest

from app.models.campaign import CampaignPlan, CampaignExecutionAttempt
from app.services import campaign_pipeline_integration as service
from app.services import campaign_execution_service as execution


@pytest.fixture
def prepared(db_session, monkeypatch):
    plan = CampaignPlan(name='验收活动', campaign_type='big88', tier='big',
        start_at=datetime(2026,11,26), end_at=datetime(2026,11,28),
        qn_campaign_title='验收活动', platform_campaign_id='12345',
        platform_united_activity_id='67890', platform_sign_record_id='123456',
        platform_activity_mode='fixed_window', status='draft')
    db_session.add(plan); db_session.commit()
    rows=[{'taobao_item_id':'111','taobao_sku_id':'222','sku_code':'X','price':100,'is_placeholder':False}]
    monkeypatch.setattr(service.cs,'build_signup_rows',lambda *_,**kw: (rows, {'rows':len(rows)}))
    monkeypatch.setattr(service.cs,'target_prices',lambda *_: {'222':{'sku_code':'X','daily':100,'target':88}})
    monkeypatch.setattr(service.campaign_policy_service,'require_policy',lambda: {'_sha256':'a'*64})
    return db_session, plan, rows


def test_contract_is_current_and_read_only(prepared):
    db,plan,_=prepared
    bundle=service.export_bundle(db,plan)
    assert bundle['all_erp_rows'][0]['big_target']==88
    assert bundle['allowed_skus']==[{'item_id':'111','sku_id':'222'}]
    assert bundle['platform_write'] is False
    assert not db.new and not db.dirty
    assert db.query(CampaignExecutionAttempt).count()==0
    assert bundle['price_version']==service.export_bundle(db,plan)['price_version']


def test_custom_baseline_from_erp_slot(prepared,monkeypatch):
    db,plan,rows=prepared;rows[0]['is_placeholder']=True
    monkeypatch.setattr(service.campaign_sku_slot_service,'immutable_baseline',lambda *a,**k:Decimal('500'))
    bundle=service.export_bundle(db,plan)
    assert bundle['all_erp_rows'][0]['custom_first_orig_price']=='500'
    assert bundle['all_erp_rows'][0]['approved_custom_price']=='100'


def test_live_switch_defaults_off(prepared,monkeypatch):
    db,plan,_=prepared;monkeypatch.delenv('PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED',raising=False)
    with pytest.raises(ValueError,match='尚未启用'):
        service.claim(db,plan.id,service.export_bundle(db,plan)['price_version'])
    assert db.query(CampaignExecutionAttempt).count()==0


def test_claim_old_and_new_mutually_protect_and_receipt_is_not_completion(prepared,monkeypatch):
    db,plan,_=prepared;monkeypatch.setenv('PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED','1')
    bundle=service.export_bundle(db,plan)
    claim=service.claim(db,plan.id,bundle['price_version'])
    with pytest.raises(ValueError,match='占位'):
        service.claim(db,plan.id,bundle['price_version'])
    old,_=execution.ensure_attempt(db,plan=plan,scope_sha256_value='b'*64)
    with pytest.raises(ValueError,match='pipeline_already_claimed'):
        execution.claim_platform_write(db,old.id,request_id='old')
    receipt={'state':'done','attempt_id':'local1','audit':{'audit_passed':True},'steps':[]}
    result=service.record_receipt(db,plan.id,claim['attempt_id'],receipt)
    assert result['business_complete'] is False
    assert plan.status=='draft'
    assert service.record_receipt(db,plan.id,claim['attempt_id'],receipt)==result
    assert service.export_bundle(db,plan)['execution_ready'] is False


def test_old_claim_blocks_new_even_on_duplicate_plan(prepared,monkeypatch):
    db,plan,_=prepared;monkeypatch.setenv('PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED','1')
    old,_=execution.ensure_attempt(db,plan=plan,scope_sha256_value='c'*64)
    execution.claim_platform_write(db,old.id,request_id='old-claim')
    duplicate = CampaignPlan(name='重复计划', campaign_type=plan.campaign_type,
        tier=plan.tier, start_at=plan.start_at, end_at=plan.end_at,
        qn_campaign_title=plan.qn_campaign_title, platform_campaign_id=plan.platform_campaign_id,
        platform_united_activity_id=plan.platform_united_activity_id,
        platform_sign_record_id=plan.platform_sign_record_id,
        platform_activity_mode='fixed_window', status='draft')
    db.add(duplicate); db.commit(); plan = duplicate
    bundle=service.export_bundle(db,plan)
    assert len(bundle['legacy_protection'])==1
    with pytest.raises(ValueError,match='占位'):
        service.claim(db,plan.id,bundle['price_version'])


def test_price_change_rejected_before_write(prepared,monkeypatch):
    db,plan,rows=prepared;monkeypatch.setenv('PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED','1')
    version=service.export_bundle(db,plan)['price_version']
    rows[0]['taobao_sku_id']='333'
    monkeypatch.setattr(service.cs,'target_prices',lambda *_:{'333':{'sku_code':'X','daily':101,'target':89}})
    with pytest.raises(ValueError,match='已变化'):
        service.claim(db,plan.id,version)
    assert db.query(CampaignExecutionAttempt).count()==0


def test_accepted_local_calc_reads_real_contract(prepared,tmp_path):
    root=Path('D:/AI/畔色ERP系统/Web-Agent程序')
    if not root.exists():pytest.skip('local accepted adapter not installed')
    spec=importlib.util.spec_from_file_location('_contract_calc',root/'app/engine/campaign_pipeline_calc.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    import json
    db,plan,_=prepared;bundle=service.export_bundle(db,plan)
    file=tmp_path/'bundle.json';file.write_text(json.dumps(bundle,ensure_ascii=False),encoding='utf-8')
    cfg=module.CampaignConfig.from_options({'campaign_id':bundle['campaign_id'],
        'erp_json_path':str(file),'discount_rate':bundle['discount_rate'],
        'erp_target_mode':bundle['campaign_tier'],'shop_name':bundle['shop'],
        'expected_price_version':bundle['price_version']},require_erp=True)
    module.validate_erp_against_config(module.load_erp_bundle(file),cfg)


def test_unknown_receipt_recovery_preserves_history(prepared, monkeypatch):
    db, plan, _ = prepared
    monkeypatch.setenv('PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED','1')
    claimed = service.claim(db, plan.id, service.export_bundle(db,plan)['price_version'])
    unknown = {'state':'unknown_pending_verify','attempt_id':'local1'}
    service.record_receipt(db,plan.id,claimed['attempt_id'],unknown)
    with pytest.raises(ValueError,match='同一个'):
        service.record_receipt(db,plan.id,claimed['attempt_id'],{'state':'done','attempt_id':'other'})
    service.record_receipt(db,plan.id,claimed['attempt_id'],{'state':'done','attempt_id':'local1'})
    row=db.get(CampaignExecutionAttempt,claimed['attempt_id'])
    assert row.write_claimed and len(row.result_summary['receipt_history'])==2
    assert row.result_summary['receipt_history'][0]['receipt']==unknown
    assert service.original_claim(db,plan.id,row.id)['receipt']['state']=='done'
    with pytest.raises(ValueError,match='明确终态'):
        service.record_receipt(db,plan.id,row.id,unknown)


def test_multiple_discount_activities_rejected(prepared):
    db,plan,_=prepared
    plan.remark='single_discount_activity_ids=11111111:123,11222222:456'
    with pytest.raises(ValueError,match='商品/SKU级'):
        service.export_bundle(db,plan)


def test_signup_readonly_path_does_not_seed_slots(db_session, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('read-only export attempted baseline creation')
    monkeypatch.setattr(service.campaign_sku_slot_service,'seed_active_slots',forbidden)
    plan=CampaignPlan(name='只读',campaign_type='big88',tier='big',
        start_at=datetime(2026,11,26),end_at=datetime(2026,11,28),status='draft')
    service.cs.build_signup_rows(db_session,plan,seed_baselines=False)
    assert not db_session.new and not db_session.dirty


def test_legacy_url_identity_matches_without_grouping_unrelated_nulls(prepared,monkeypatch):
    db,plan,_=prepared
    other=CampaignPlan(name='不同活动',campaign_type='big88',tier='big',status='draft')
    db.add(other); db.commit()
    plan.platform_campaign_id=None
    monkeypatch.setattr(service.cs,'campaign_identity',lambda p: {'campaign_id':str(p.id)})
    old,_=execution.ensure_attempt(db,plan=other,scope_sha256_value='d'*64)
    old.write_claimed=True;db.commit()
    assert not service.protected_attempts(db,plan)


def test_discount_ownership_blocks_new_writer_without_breaking_legacy(prepared,monkeypatch):
    db,plan,_=prepared
    service.protect_legacy_discount(db,plan,committing=True)
    service.protect_legacy_discount(db,plan,committing=True)
    assert db.query(CampaignExecutionAttempt).count()==1
    assert not service.export_bundle(db,plan)['execution_ready']


def test_new_claim_prevents_legacy_discount_upload(prepared,monkeypatch):
    db,plan,_=prepared
    monkeypatch.setenv('PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED','1')
    service.claim(db,plan.id,service.export_bundle(db,plan)['price_version'])
    result=service.cs._upload_and_wait(db,'single_item_discount','commit',b'',None,None,plan=plan)
    assert result['step']=='pipeline_owner_guard'

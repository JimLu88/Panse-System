from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_replacement_audit as c

@pytest.fixture(autouse=True)
def isolated_protection(monkeypatch):
    monkeypatch.setattr(c,'load_current_protection',lambda: {})

def row(**updates):
    return dict(dict(item='123456789012',sku='123456789013',custom=False,target='700.00',
        big_target='650.00',activity_price='1000.00',list_price='1100.00',cap='699.00',
        final='701',effective_mode='unknown'),**updates)

def request(**updates):
    return dict(dict(schema='single_discount_replacement_audit_v1',end=c.END,target='medium',
        campaign='legacy/itemApply/3172207691',user_authorization='test exact replacement',
        platform_write=False,rows=[row()]),**updates)

def test_distinct_bases_and_frozen_cap_target():
    r=c.row_audit(row())
    assert r['candidates']['official_10']['deduct']=='201.00'
    assert r['candidates']['official_10']['final']=='699.00'
    assert r['candidates']['no_official']['deduct']=='400.00'
    assert r['selected_candidate'] is None and not r['upload_ready']

@pytest.mark.parametrize('value',[None,'','NaN','Infinity','0','-1','700.001'])
def test_missing_invalid_target_is_not_zero(value):
    r=c.row_audit(row(target=value))
    assert not r['candidates']

def test_no_sales_does_not_prove_no_official_mode():
    r=c.row_audit(row(no_sales_this_campaign=True))
    assert r['selected_candidate'] is None and r['notes']

def test_custom_never_becomes_zero_or_ordinary_discount():
    for flag in (True,None):
        r=c.row_audit(row(custom=flag))
        assert not r['candidates'] and r['blockers']

def test_missing_cap_does_not_prevent_only_conditional_no_official_math():
    r=c.row_audit(row(cap=None,final=None))
    assert 'official_10' not in r['candidates'] and 'no_official' in r['candidates']
    assert 'current_final_readback_missing' in r['blockers']

def test_more_than_two_requires_rotation_not_silent_lowering():
    r=c.row_audit(row(cap='697.99'))
    assert 'requires_rotation' in r['candidate_errors']['official_10']

def test_exact_two_allowed_and_official_rounding():
    r=c.row_audit(row(cap='698.00',activity_price='1000.01'))
    assert r['candidates']['official_10']['official_cut']=='101'
    assert r['candidates']['official_10']['deduct']=='201.01'

def test_no_official_target_cannot_exceed_g():
    r=c.row_audit(row(list_price='699.99'))
    assert 'no_official' not in r['candidates']

def test_zero_deduction_is_explicit_no_need_not_missing():
    r=c.row_audit(row(list_price='700.00'))
    assert r['candidates']['no_official']['deduct']=='0.00'

def test_even_verified_mode_never_releases_upload():
    r=c.audit(request(rows=[row(effective_mode='official_10',effective_mode_evidence='test receipt')]))
    assert r['counts']['selected_candidates']==1 and not r['upload_ready'] and r['not_registered']

@pytest.mark.parametrize('field,value',[('end','2026-10-07 23:59:59'),('target','big'),
    ('campaign','double11'),('platform_write',True),('user_authorization','')])
def test_wrong_window_or_authority_refused(field,value):
    with pytest.raises(ValueError):c.audit(request(**{field:value}))

def test_missing_member_visible_and_next_campaign_untouched():
    old=dict(item='123456789012',sku='123456789099',offer_id='146901969223',end=c.END)
    result=c.audit(request(old_offer_members=[old],old_offer_scope_verified=True))
    assert result['missing_old_offer_members']==[('123456789012','123456789099')]
    assert not result['all_affected_members_accounted_for']
    old['end']='2026-10-11 23:59:59'
    with pytest.raises(ValueError):c.audit(request(old_offer_members=[old]))

def test_454_current_rows_alone_are_not_complete_old_offer_scope():
    result=c.audit(request())
    assert not result['all_affected_members_accounted_for'] and not result['old_offer_scope_verified']

def test_current_success_protects_candidates_even_with_pricing_evidence():
    r=row(effective_mode='official_10',effective_mode_evidence='test receipt')
    result=c.audit(request(rows=[r]),protection={'protected_pairs':[(r['item'],r['sku'])]})
    assert result['counts']['selected_candidates']==0 and result['protected_candidate_skus']==1
    assert result['rows'][0]['discount_retry_allowed'] is False
    assert result['rows'][0]['candidates']  # retained for price review only

def receipts():
    p=dict(end=c.END,sha256='a'*64,rows=[dict(item='123456789012',sku=str(123456780000+i)) for i in range(327)])
    o=dict(offer_id='147717819883',import_history_id='1',import_time_local='2026-09-28 04:02:03',
           import_status='导入完成',success_count=327,failure_count=0,
           candidate_workbook_sha256='a'*64,workbook_bytes_bound_by_screenshot=False)
    return o,p

def test_screenshot_count_never_becomes_final_price_or_file_identity_proof():
    o,p=receipts();result=c.protection_from_receipts(o,p)
    assert len(result['protected_pairs'])==327
    for key in ('submitted_file_bytes_verified','actual_offer_window_verified','final_prices_verified','super_reduce_enrollment_verified'):
        assert result[key] is False

@pytest.mark.parametrize('key,value',[('offer_id','123'),('success_count',326),('failure_count',1),
    ('candidate_workbook_sha256','b'*64),('workbook_bytes_bound_by_screenshot',True)])
def test_inconsistent_success_receipt_never_silently_weakens_protection(key,value):
    o,p=receipts();o[key]=value
    with pytest.raises(ValueError):c.protection_from_receipts(o,p)

def test_duplicate_candidate_scope_rejected():
    o,p=receipts();p['rows'][1]=p['rows'][0]
    with pytest.raises(ValueError):c.protection_from_receipts(o,p)

from decimal import Decimal as D
import pytest
import repair_core as c

def normal(target='100.00',daily='200.00'):
    return dict(custom=False,daily=daily,big_target=target)
def offer(deduct='76.00'):
    return dict(deduct=deduct,offer_id='123456789',mode='SKU级')
def error(current='100.00',cap='99.00',kind='coupon_cap'):
    return dict(current=current,cap=cap,kind=kind)
def basis(original='1000.00'):
    return dict(original=original,floor=str(D(original)*D('.20')),source='fixed')

def test_normal_adjusts_existing_discount_not_signup_or_target():
    r=c.decide(normal(),'200.00',[error()],offer())
    assert r['action']=='original_discount' and r['new_deduct']=='77.00'
    assert r['new_price']==r['old_price']=='200.00' and r['delta']=='-1.00'

def test_tolerance_is_cumulative_from_erp_not_previous_price():
    with pytest.raises(ValueError,match='frozen_target_tolerance'):
        c.decide(normal(),'200.00',[error('99.00','97.90')],offer('77.00'))

def test_exact_two_yuan_boundary_allowed():
    assert c.decide(normal(),'200.00',[error(cap='98.00')],offer())['delta']=='-2.00'

def test_ordinary_activity_cap_cannot_be_treated_as_coupon_cap():
    with pytest.raises(ValueError,match='activity_cap_requires_rotation'):
        c.decide(normal(),'200.00',[error('200.00','199.00','activity_price_cap')],offer())

def test_erp_custom_accessory_can_lower_activity_price_to_283_99():
    erp=dict(custom=True,daily='1000.00',big_target=None)
    r=c.decide(erp,'289.51',[error('289.51','283.99','activity_price_cap')],dict(deduct='0',offer_id=None),basis())
    assert r['new_price']=='283.99' and r['fixed_floor']=='200.0000'

def test_custom_floor_is_fixed_not_current_reduced_price():
    with pytest.raises(ValueError,match='below_fixed_floor'):
        c.decide(dict(custom=True,daily='1000.00'),'500.00',[error('440.00','170.00')],dict(deduct='0'),basis())

def test_custom_exact_floor_can_satisfy_176_coupon_cap():
    r=c.decide(dict(custom=True,daily='1000.00'),'511.36',[error('449.36','176.00')],dict(deduct='0'),basis())
    assert D(r['new_price'])>=200 and D(r['final'])<=176

def test_rounded_custom_below_100_checks_actual_cent_formula():
    price,final=c.custom_candidate('250.00',basis('250.00'),'0',{'coupon_cap':D('63.06')})
    assert price>=50 and final<=D('63.06') and price-c.official_cut(price,c.RATE)==final

def test_current_coupon_must_match_current_discount_and_activity():
    with pytest.raises(ValueError,match='formula_conflict'):
        c.decide(normal(),'200.00',[error('105.00','99.00')],offer())

def test_missing_existing_offer_does_not_create_one():
    with pytest.raises(ValueError,match='existing_sku_offer_required'):
        c.decide(normal(),'200.00',[error()],dict(deduct='76.00',offer_id=None,mode=None))

def test_unknown_custom_classification_does_not_guess_from_name():
    with pytest.raises(ValueError,match='classification_unknown'):
        c.decide(dict(custom=None,daily='200',big_target='100'),'200',[error()],offer())

def test_parse_official_coupon_uses_restrictive_actual_cap():
    rows=[['6276228467051','规格','693.88','最低券后价 693.58\nN折后最低券后价 694.00','建议']]
    r=c.parse_error_table(['skuId','sku名称','活动普惠券后价','最低普惠券后价','建议'],rows,'1','2')
    assert r[0]['cap']=='693.58' and r[0]['kind']=='coupon_cap'

def test_parse_activity_cap_never_reads_dates_or_other_price_columns():
    r=c.parse_error_table(['skuId','sku名称','活动价','校验期最低标价','最低标价优惠'],[['59165186664\n44','配件','289.51','283.99','2025-06-21至2028-07-31']],'1','2')
    assert r[0]['sku']=='5916518666444' and r[0]['kind']=='activity_price_cap' and r[0]['cap']=='283.99'

def test_missing_price_cap_not_guessed():
    with pytest.raises(ValueError,match='cap_not_explicit'):
        c.parse_error_table(['skuId','活动价','校验期最低标价'],[['5916518666444','289.51','加载中']],'1','2')

def test_unknown_discount_membership_not_assumed_zero():
    data=dict(rows=[dict(item='1',mode=m,list_coverage_verified=True,offers=[dict(overlaps_requested_window=True,sku_amounts_verified=True,values={},not_enrolled=[])]) for m in ('商品级','SKU级')])
    with pytest.raises(ValueError,match='membership_unknown'):c.discount_for(data,'1','2')

def test_final_blank_target_coupon_is_unknown_not_success():
    before=[dict(item='1',sku='2',marketing_id='3',activity_price='200',coupon_price='100',state='异常')]
    after=[dict(before[0],coupon_price='',state='活动中')]
    plan=dict(decisions=[dict(item='1',sku='2',marketing_id='3',action='original_discount',new_price='200',final='99')])
    assert not c.audit(before,after,plan)['all_verified']

def test_normal_untargeted_price_change_fails_final_audit():
    before=[dict(item='1',sku='2',marketing_id='3',activity_price='200',coupon_price='100',state='活动中')]
    assert not c.audit(before,[dict(before[0],activity_price='199')],dict(decisions=[]))['all_verified']

def test_final_missing_row_is_not_silently_dropped():
    before=[dict(item='1',sku='2',marketing_id='3',activity_price='200',coupon_price='100',state='活动中')]
    assert not c.audit(before,[],dict(decisions=[]))['coverage_equal']

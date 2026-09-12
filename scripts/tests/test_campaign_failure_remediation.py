from copy import deepcopy
from pathlib import Path
import sys
import hashlib

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_continuous_policy import classify,classify_items
from campaign_feedback_normalization import normalize_errors
from campaign_failure_remediation import corrected_scope
from campaign_continuous_transport import persist
from campaign_entry_authority import file_sha
from campaign_official_failure_report import parse_report
from test_campaign_official_failure_report import package


def parse(message,name='颜色分类:定制;床板材质:榉木;'):
    raw=package([['1','11',name,'1000','失败',message]])
    return parse_report(raw,expected_sha=hashlib.sha256(raw).hexdigest(),batch='123',expected_failed_items=['1'])


def test_no_sales_product_priority_does_not_require_sku_or_price():
    parsed=parse('动销不达标，不予准入。您的sku：定制;榉木 在管控期标价为10元；')
    errors=normalize_errors(parsed,submitted_rows=[],erp_rows=[],fixed_bases={},actual_discounts=[],rate='.1')
    assert len(errors)==1 and classify(errors[0])['action']=='not_eligible_this_campaign'


@pytest.mark.parametrize('cap,original,action,price',[
    ('397.00','1000','repair','396.99'),('200.01','1000','repair','200.00'),
    ('200.00','1000','rotation_approval',None),('108.35','541.67','repair','108.34'),
    ('108.34','541.67','rotation_approval',None)])
def test_strict_approved_cap_and_fixed_twenty_percent(cap,original,action,price):
    parsed=parse(f'当前商品活动价可编辑，且需小于审核通过值（{cap}），skuId：11')
    errors=normalize_errors(parsed,submitted_rows=[dict(item='1',sku='11',erp_code='C',activity_price='1000')],
        erp_rows=[dict(code='C',custom=True,daily='1000')],
        fixed_bases={('1','11'):dict(original=original,source_sha256='proof')},actual_discounts=[],rate='.1')
    decision=classify(errors[0]);assert decision['action']==action
    if price:assert decision['repair']['price']==price


def test_named_attribute_prefixes_match_values():
    parsed=parse('您的sku：定制;榉木 在管控期标价为300元；')
    assert len(parsed['errors'])==1 and parsed['errors'][0]['sku']=='11'


def test_invalid_id_requires_complete_export_before_exclusion():
    error=parse('数据解析失败，SKUID=11不属于当前商品或已下架（已下架SKU不支持报名）')['errors'][0]
    assert classify(error)['action']=='manual'
    error.update(full_official_export_verified=True,remove_from_signup=True,mapping_scope_evidence={'path':'proof','sha256':'x'})
    assert classify(error)['repair']['kind']=='exclude_ineligible_sku'


def test_scope_replaces_only_failed_item_and_preserves_zero_stock(tmp_path):
    def fact(i,s,stock='0'):
        return {'facts':dict(item=i,sku=s,stock=stock),'sources':[{'sha256':'source'}]}
    original={'complete':True,'page_evidence':'old','sku_facts':[fact('1','11'),fact('2','21')]}
    fresh={'complete':True,'page_evidence':'new','sku_facts':[fact('1','11'),fact('1','12'),fact('2','22')]}
    p=persist(tmp_path/'scope.json',{'scope':fresh,'excluded':[dict(item='1',sku='11')]})
    corrections={'1':[{'repair':{'kind':'exclude_ineligible_sku','scope_evidence':{'path':p,'sha256':file_sha(p)}}}]}
    result,mappings=corrected_scope(original,corrections)
    assert {(e['facts']['item'],e['facts']['sku']) for e in result['sku_facts']}=={('1','12'),('2','21')}
    assert mappings[0]['facts']['stock']=='0' and original['sku_facts'][0]['facts']['sku']=='11'


@pytest.mark.parametrize('delta,action',[('2','repair'),('2.01','rotation_approval')])
def test_ordinary_threshold_is_inclusive_and_never_rotates(delta,action):
    from decimal import Decimal
    e=dict(item='1',sku='11',terminal='failed',batch='123',official_evidence='proof',kind='coupon_price',
        custom=False,erp_daily='100',submitted_price='100',erp_final_target='70',feasible_final_price=str(Decimal('70')-Decimal(delta)))
    assert classify(e)['action']==action

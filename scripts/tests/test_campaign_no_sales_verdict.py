"""The official rule paragraph is not evidence of this item's zero sales."""
import hashlib
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_official_failure_report as report

ITEM='720234422814'
SKU='1234567890123'
NAME='双人位2米;茶叶绿'
RULE='参加本次活动的商品要求满足“动销”校验基础准入门槛要求，近60天销售件数≥1件。系统实时校验，不满足条件的商品不予准入。'
PRICE='您的以下sku不满足该要求：['+NAME+'（活动普惠券后价：5028.50元，最低普惠券后价：3389.95元，10.0折折后券后价：3389.95元）]'


def parsed(monkeypatch,message):
    rows={1:{'A':'商品ID','B':'SKUID','C':'SKU名称','D':'活动价','E':'是否成功','F':'失败原因或风险提示'},
          2:{'A':ITEM,'B':SKU,'C':NAME,'D':'6000.00','E':'失败','F':message}}
    monkeypatch.setattr(report,'read_rows',lambda raw,sheet:rows)
    raw=b'fixture-official-report';digest=hashlib.sha256(raw).hexdigest()
    return report.parse_feedback(raw,expected_sha=digest,batch='123',expected_items=[ITEM])


def test_generic_no_sales_rule_does_not_hide_exact_price_failure(monkeypatch):
    result=parsed(monkeypatch,RULE+'\n'+PRICE)
    assert [e['kind'] for e in result['errors']]==['coupon_price']
    assert result['errors'][0]['sku']==SKU
    assert result['errors'][0]['official_cap']=='3389.95'


def test_official_product_zero_sales_verdict_blocks_whole_item(monkeypatch):
    result=parsed(monkeypatch,RULE+'\n您的资质近60天销售件数0件，活动要求大于等于1\n'+PRICE)
    assert len(result['errors'])==1
    assert result['errors'][0]['kind']=='no_sales' and result['errors'][0]['sku']==''


def test_generic_rule_only_is_unknown_not_false_no_sales(monkeypatch):
    result=parsed(monkeypatch,RULE)
    assert len(result['errors'])==1
    assert result['errors'][0]['kind']=='unknown'
    assert result['errors'][0]['parse_issue']=='unparsed_or_incomplete_official_failure'

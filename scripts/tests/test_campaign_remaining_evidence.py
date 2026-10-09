import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_remaining_evidence import category

def test_existing_is_not_new_success_or_retry():
    assert category(['商品已报名，且不是草稿态，不支持批量导入，请到商品编辑页手动更新'])=='已有报名，不可重复批量导入'
    assert category([])=='提交前事实缺口'

def test_mixed_messages_not_hidden():
    assert category(['商品已报名，且不是草稿态，不支持批量导入，请到商品编辑页手动更新','另一失败'])=='需核对完整官方原因'

def test_truncated_price_not_invented():
    assert '疑似截断' in category(['[SKU（活动普惠券后价：850.00元，最低普惠券后价：340.'])

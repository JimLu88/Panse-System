"""输入质量门控单元测试（实施计划 case_01～case_02）。"""

from apps.core.ai.input_quality_gate import (
    check_buyer_input,
    is_datetime_only_noise,
    is_metadata_noise,
    is_product_card_noise,
)


def test_metadata_noise_samples():
    assert is_metadata_noise("￥99999.00库存406销量4")
    assert is_metadata_noise("孚格家居2026-5-1600:16:25")
    assert check_buyer_input("e").action == "quick_reply"
    assert check_buyer_input("Foryou").action == "quick_reply"
    assert check_buyer_input("排序").action == "quick_reply"


def test_normal_question_passes():
    r = check_buyer_input("孚格北欧阿尔托这款沙发有扶手款吗")
    assert r.action == "pass"


def test_product_card_noise_filtered():
    assert is_product_card_noise("Foryou ￥1000.0")
    assert not is_product_card_noise("这款 ￥1000 有扶手吗")
    assert check_buyer_input("Foryou ￥99999.00").action == "discard_log"


def test_datetime_only_noise():
    assert is_datetime_only_noise("2026-5-17 20:46:54")
    assert check_buyer_input("2026-5-17 20:46:54").action == "discard_log"

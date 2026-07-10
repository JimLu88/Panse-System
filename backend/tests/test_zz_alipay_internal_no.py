# -*- coding: utf-8 -*-
"""支付宝内部号形态判非订单引用 (2026-07-10 根治爱群号/佳宝号655条"待补还原规则"僵尸提示)。
(zz_ 前缀同 test_zz_purchase_payment_shared_flow: 绕开既有 SQLite 连接污染的排序坑。)"""
from app.services.order_no_normalizer import is_non_order_reference


def test_alipay_internal_numbers_are_non_order():
    """账务流水号(32位)/交易号(28位)——日期开头长纯数字 → 非订单引用, 不再计"待补规则"。"""
    assert is_non_order_reference("20260421200040011100330070643754")   # 账务流水号 32位
    assert is_non_order_reference("2026042111120520426700428855610")    # 31位
    assert is_non_order_reference("2026042122001488331414336560")       # 交易号 28位
    assert is_non_order_reference("20260423200040011100680090321541")   # 佳宝号样例


def test_real_taobao_order_numbers_untouched():
    """真淘宝单号(18-19位, 即便 20 开头)绝不误伤; 短数字也不误伤。"""
    assert not is_non_order_reference("3307941483418122285")     # 19位真单
    assert not is_non_order_reference("2026042120004001110")     # 19位、碰巧日期开头 → 不判(<25位)
    assert not is_non_order_reference("4990013425203542801")     # 19位真单
    assert not is_non_order_reference("12345678")                # 短数字

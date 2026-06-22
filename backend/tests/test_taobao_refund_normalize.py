# -*- coding: utf-8 -*-
"""根治"优惠/取消产品被当退款双扣": _normalize_refund 规则 (2026-06-22)。

应付−实付≈退款 → 该"退款"已含在实付净额里, 非真退款, 归 0; 真退款(应付=实付)保留。
"""
from decimal import Decimal

from app.services.taobao_order_import import _normalize_refund

D = Decimal


def test_discount_misrecorded_as_refund_zeroed():
    # 3300165627049005492 实例: 应付2711.05 − 实付2211.05 = 退款500 → 优惠, 归0
    assert _normalize_refund(D("2711.05"), D("2211.05"), D("500.00")) == D("0")


def test_multiproduct_cancelled_subitem_zeroed():
    # 5115783 实例: 应付4688.67 − 实付989.67 = 退款3699 (取消的箱体床) → 归0
    assert _normalize_refund(D("4688.67"), D("989.67"), D("3699.00")) == D("0")


def test_real_refund_preserved():
    # 买家付了全款(应付=实付)后才退 → 真退款, 保留, 不误伤
    assert _normalize_refund(D("2711.05"), D("2711.05"), D("500.00")) == D("500.00")


def test_gap_not_equal_refund_preserved():
    # 应付−实付(¥100) ≠ 退款(¥500) → 非此模式, 原样保留
    assert _normalize_refund(D("2811.05"), D("2711.05"), D("500.00")) == D("500.00")


def test_zero_or_none_refund_unchanged():
    assert _normalize_refund(D("2711"), D("2211"), D("0")) == D("0")
    assert _normalize_refund(D("2711"), D("2211"), None) is None
    assert _normalize_refund(None, D("2211"), D("500")) == D("500")

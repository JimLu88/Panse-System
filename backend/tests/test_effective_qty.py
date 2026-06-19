"""qty bug 修复测试: theoretical_cost 改存订单总额(单件×真实计价件数)。

用户拍板 2026-06-20: 修「买N件只算1件成本」, 排除定制单, 脏数量单(拍N件凑价)不乘。
"""
from decimal import Decimal
from types import SimpleNamespace

from app.services.order_cost_service import _effective_qty


def _o(qty, paid, is_custom=False):
    return SimpleNamespace(qty=qty, paid_amount=Decimal(str(paid)), is_custom=is_custom)


def test_real_multi_unit_multiplies():
    # 床头柜: qty=2, 件均实付 ¥1055 ≥ 单件成本 ¥820 → 真多件, ×2
    assert _effective_qty(_o(2, 2110), Decimal("820")) == 2


def test_dirty_qty_link_order_not_multiplied():
    # 餐边柜: qty=16(拍16件凑价), 件均 ¥669 < 单件成本 ¥8721 → 脏数量, ×1
    assert _effective_qty(_o(16, 10699), Decimal("8721")) == 1


def test_dirty_qty_table_not_multiplied():
    # 餐桌: qty=4, 件均 ¥681 < 单件成本 ¥1898 → ×1
    assert _effective_qty(_o(4, 2722), Decimal("1898")) == 1


def test_custom_order_excluded():
    # 定制单一律排除(qty 多为定金链接占位)
    assert _effective_qty(_o(5, 3661, is_custom=True), Decimal("1889")) == 1


def test_qty_one_unchanged():
    assert _effective_qty(_o(1, 2000), Decimal("820")) == 1


def test_zero_cost_not_multiplied():
    # 差价/赠品单 单件成本 0 → ×1(避免 0×N 无意义 & 防脏 qty)
    assert _effective_qty(_o(88, 75), Decimal("0")) == 1


def test_exact_break_even_multiplies():
    # 件均实付 恰等于 单件成本 → 视为真多件
    assert _effective_qty(_o(3, 2460), Decimal("820")) == 3

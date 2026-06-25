# -*- coding: utf-8 -*-
"""打包费重复计算修复回归 (2026-06-26)。

根因: 定价表 PricingSku.physical_cost 已含 packaging_cost, theoretical_cost = physical_cost × 件数
故含打包; 但 physical_cost_breakdown 从 theoretical 派生 estimate_part 后又单独 + packing → 打包算两次。
修复: 凡 estimate_part 从 theoretical 派生的分支(非定制 reconstruct / 非定制 推演)先减掉嵌入的 est_packing,
再统一 + packing 一次。定制单 theoretical 来自定制报价/兜底(不含打包)不减; 定制回填走木作占比(分母已排打包)。

每个用例断言: 打包恰好算一次(不是 0, 不是 2)。
"""
from decimal import Decimal

from app.models.order import Order
from app.services import order_financials as ofin


# ───────── 非定制 推演单 (actual_cost 空, theoretical=定价表physical 含打包) ─────────

def test_noncustom_estimate_packing_counted_once():
    """非定制推演: theoretical=2080(含打包180) → 商品成本=2080(打包一次), 不是2260(两次)。"""
    o = Order(order_no="P1", is_custom=False, sku_code="PPS001", actual_cost=None,
              paid_amount=Decimal("5000"), theoretical_cost=Decimal("2080"), est_packing=Decimal("180"))
    assert ofin.physical_cost(o) == Decimal("2080")


def test_noncustom_estimate_actual_packing_swaps():
    """非定制推演 + 实际打包账单200: 嵌入预估180被减、实际200算一次 → 2080-180+200=2100。"""
    o = Order(order_no="P2", is_custom=False, sku_code="PPS001", actual_cost=None,
              paid_amount=Decimal("5000"), theoretical_cost=Decimal("2080"),
              est_packing=Decimal("180"), actual_packing=Decimal("200"))
    assert ofin.physical_cost(o) == Decimal("2100")


def test_noncustom_no_packing_unchanged():
    """该SKU打包费=0(est_packing=0): 减0、加0 → 商品成本=theoretical 不变。"""
    o = Order(order_no="P3", is_custom=False, sku_code="PPS001", actual_cost=None,
              paid_amount=Decimal("9000"), theoretical_cost=Decimal("3312"), est_packing=Decimal("0"))
    assert ofin.physical_cost(o) == Decimal("3312")


# ───────── 非定制 回填重算单 (actual_cost + wood_cost_est, reconstruct) ─────────

def test_noncustom_reconstruct_packing_once():
    """回填: 工厂木作2800 + (定价4191.04−木作2800−嵌入打包170) + 打包170 = 4191.04(打包一次)。
    修复前会是 4361.04(打包两次)。"""
    o = Order(order_no="P4", is_custom=False, sku_code="PPS002", actual_cost=Decimal("2800"),
              wood_cost_est=Decimal("2800"), theoretical_cost=Decimal("4191.04"),
              paid_amount=Decimal("8000"), est_packing=Decimal("170"))
    assert ofin.physical_cost(o) == Decimal("4191.04")


def test_noncustom_reconstruct_actual_packing():
    """回填 + 实际打包340(截图#15): 2800 + (4191.04−2800−170) + 340 = 4361.04, 比修前少嵌入的170。"""
    o = Order(order_no="P5", is_custom=False, sku_code="PPS002", actual_cost=Decimal("2800"),
              wood_cost_est=Decimal("2800"), theoretical_cost=Decimal("4191.04"),
              paid_amount=Decimal("8000"), est_packing=Decimal("170"), actual_packing=Decimal("340"))
    assert ofin.physical_cost(o) == Decimal("4361.04")


# ───────── 定制 推演单 (actual_cost 空): theoretical 来自定制报价/兜底, 不含打包 → 不减(打包加一次是对的) ─────────

def test_custom_estimate_packing_not_subtracted():
    """定制未回填: theoretical=3811.31(定制报价, 不含打包) + 打包170 = 3981.31(打包一次)。
    这正是之前被误判'重复'的假阳性: 定制推演本就不含打包, 不该减。"""
    o = Order(order_no="P6", is_custom=True, actual_cost=None,
              paid_amount=Decimal("4000"), theoretical_cost=Decimal("3811.31"), est_packing=Decimal("170"))
    assert ofin.physical_cost(o) == Decimal("3981.31")


# ───────── 非定制 else 分支 (actual_cost 但无定价参照): estimate_part=物流+安装, 本就无嵌入打包, 打包加一次 ─────────

def test_noncustom_else_branch_packing_once():
    """回填无定价参照(wood_est=0): 木作600 + (物流100+安装50) + 打包80 = 830(打包一次, 分支本就无嵌入打包)。"""
    o = Order(order_no="P7", is_custom=False, sku_code="PPS900", actual_cost=Decimal("600"),
              wood_cost_est=Decimal("0"), theoretical_cost=None, paid_amount=Decimal("5000"),
              est_logistics=Decimal("100"), est_install=Decimal("50"), est_packing=Decimal("80"))
    assert ofin.physical_cost(o) == Decimal("830")


# ───────── 封顶单: 打包改动不影响(成本被封到 实付×0.85) ─────────

def test_capped_fragment_unaffected_by_packing_fix():
    """定金片段(实付100 << 成本): 仍 实付×85%=85, 打包修复不改封顶结果。"""
    o = Order(order_no="P8", is_custom=True, paid_amount=Decimal("100"),
              actual_cost=Decimal("63"), wood_cost_est=Decimal("3000"), theoretical_cost=Decimal("63"),
              est_logistics=Decimal("350"), est_packing=Decimal("170"))
    assert ofin.physical_cost(o) == Decimal("85.00")


# ───────── breakdown 自洽: packing 项始终只进 final 一次 ─────────

def test_breakdown_packing_in_estimate_removed():
    """非定制推演 breakdown: estimate_part 已不含打包(=theoretical−est_pack), packing 单列, 合计只算一次。"""
    o = Order(order_no="P9", is_custom=False, sku_code="PPS001", actual_cost=None,
              paid_amount=Decimal("9000"), theoretical_cost=Decimal("2080"), est_packing=Decimal("180"))
    b = ofin.physical_cost_breakdown(o)
    assert b["estimate_part"] == Decimal("1900")   # 2080 − 180(嵌入打包)
    assert b["packing"] == Decimal("180")
    assert b["final"] == Decimal("2080") == b["factory_wood"] + b["estimate_part"] + b["packing"]

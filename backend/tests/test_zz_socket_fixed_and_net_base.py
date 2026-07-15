# -*- coding: utf-8 -*-
"""两修 (用户拍板 2026-07-14):

A. 封顶/兜底基数 = 真实收入(实付−退款) — 一单两件退一件, 兜底85曾按毛实付把退掉那件的成本也背上;
B. 插座追加固定价: 不限产品名, 备注纯插座 + 毛实付为100倍数 → 55×数量+运费8, 账单一概不吃
   (数量: 备注显式优先, 没写按 实付/100 推)。
"""
from decimal import Decimal

from app.models.order import Order
from app.services import order_financials as ofin


# ── A: 净收入基数 ────────────────────────────────────────────────
def test_floor_base_uses_net_revenue():
    """定制兜底85: 实付4000退1000 → 基数3000 → 兜底 2550 (旧口径按毛4000会兜到3400)。"""
    o = Order(order_no="NB1", is_custom=True, paid_amount=Decimal("4000"),
              refund_amount=Decimal("1000"), actual_cost=Decimal("800"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "定制兜底85"
    assert pb["final"] == Decimal("2550.00")


def test_cap_base_uses_net_revenue():
    """推演封顶85: 实付1000退800 → 净200, 推演5000>200 → 封顶 170。"""
    o = Order(order_no="NB2", is_custom=False, paid_amount=Decimal("1000"),
              refund_amount=Decimal("800"), theoretical_cost=Decimal("5000"),
              product_name="畔色餐边柜")
    pb = ofin.physical_cost_breakdown(o)
    assert pb["final"] == Decimal("170.00")


def test_no_refund_unchanged():
    """无退款单行为不变(回归): 兜底仍 实付×85%。"""
    o = Order(order_no="NB3", is_custom=True, paid_amount=Decimal("4000"),
              actual_cost=Decimal("2010"))
    assert ofin.physical_cost(o) == Decimal("3400.00")


# ── B: 插座追加固定价 ────────────────────────────────────────────
def test_socket_single_no_qty_by_paid():
    """备注没写数量, 实付100 → 数量=100/100=1 → 55+8=63。"""
    o = Order(order_no="SK1", paid_amount=Decimal("100"), remark="追加插座",
              product_name="畔色北欧实木餐边柜")
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "插座追加固定"
    assert pb["final"] == Decimal("63.00")


def test_socket_qty_from_remark():
    """备注显式"两个插座", 实付200 → 55×2+8=118。"""
    o = Order(order_no="SK2", paid_amount=Decimal("200"), remark="两个插座",
              product_name="畔色实木岩板餐边柜")
    assert ofin.physical_cost(o) == Decimal("118.00")


def test_socket_qty_inferred_from_paid():
    """备注没写数量, 实付200 → 数量=2 → 118。"""
    o = Order(order_no="SK3", paid_amount=Decimal("200"), remark="追加插座",
              product_name="畔色实木岩板餐边柜")
    assert ofin.physical_cost(o) == Decimal("118.00")


def test_socket_ignores_bills():
    """账单一概不吃: 挂着物流567/打包480/实配件75 → 仍 63 (…92909 案)。"""
    o = Order(order_no="SK4", paid_amount=Decimal("100"), remark="追加插座",
              actual_logistics=Decimal("567"), actual_packing=Decimal("480"),
              actual_parts=Decimal("75"), wood_cost_est=Decimal("2600"),
              est_parts=Decimal("380"), product_name="畔色北欧实木餐边柜")
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "插座追加固定"
    assert pb["final"] == Decimal("63.00")
    assert pb["logistics_component"] == Decimal("0")


def test_non_hundred_multiple_excluded():
    """实付非100倍数(123) → 不进固定价分支。"""
    o = Order(order_no="SK5", paid_amount=Decimal("123"), remark="追加插座",
              product_name="畔色餐边柜")
    assert ofin.physical_cost_breakdown(o)["cap_mode"] != "插座追加固定"


def test_socket_mixed_big_item_excluded():
    """插座混大件("上柜背板有插座") → 大件关键词排除, 不误伤整柜真单。"""
    o = Order(order_no="SK6", paid_amount=Decimal("5600"), remark="上柜背板内缩,背面有插座",
              actual_cost=Decimal("4000"), product_name="畔色餐边柜")
    assert ofin.physical_cost_breakdown(o)["cap_mode"] != "插座追加固定"

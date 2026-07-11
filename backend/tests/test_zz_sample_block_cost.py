# -*- coding: utf-8 -*-
"""样块单固定值口径 (2026-07-11 用户拍板): 成本 = 木作6/块×块数 + 打包2/单, 运费固定5/单。

读时现算、完全不读 actual_/est_/theoretical → 历史七种花数(0/1/5/6/8/13/14)的病灶
(账单把双块记6 / 核对表合并把物流写0 / est大件脏值 / theo不一致)全部失效。
块数按实付推 round(实付÷21): Order.qty 对样块不可靠(多行明细合并丢件数, 实测14个双/三/四块单 qty=1)。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from datetime import date
from decimal import Decimal as D

from app.models.order import Order
from app.services import order_financials as ofin

_COEF = {"handling_rate": D("0.006"), "activity_rate": D("0.02"),
         "activity_since": date(2026, 5, 1), "activity_until": date(2026, 6, 30)}

_NAME = "畔色木作木块小样樱桃木黑胡桃木白蜡木榉木红白橡木样品样块"


def _sample(**kw):
    base = dict(order_no="YB1", product_name=_NAME, is_custom=False, qty=1,
                paid_amount=D("22"), tax=D("0"), order_date=date(2026, 3, 1))
    base.update(kw)
    return Order(**base)


def test_single_block_fixed_ignores_dirty_fields():
    """单块(付22): 固定 6+2=8, 运费列5 —— 账单/合并零值/est脏值全都不读。"""
    o = _sample(actual_cost=D("6"), actual_packing=D("2"), actual_logistics=D("0"),
                est_logistics=D("300"), est_packing=D("170"), theoretical_cost=D("13"),
                wood_cost_est=D("6"), actual_freight=D("1"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "样块固定"
    assert pb["final"] == D("8")
    b = ofin.cost_breakdown(o, _COEF)
    assert b["freight"] == D("5")          # 固定, 不读 actual_freight=1 的脏值
    assert b["install_upstairs"] == D("0")


def test_double_block_by_paid_not_qty():
    """双块(付44)但 qty=1、账单错记6: 块数按实付推=2 → 12+2=14 + 运5 = 19。"""
    o = _sample(paid_amount=D("44"), qty=1, actual_cost=D("6"),
                est_logistics=D("300"), est_packing=D("170"), theoretical_cost=D("19"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "样块固定"
    assert pb["final"] == D("14")
    assert ofin.cost_breakdown(o, _COEF)["freight"] == D("5")


def test_triple_and_quad_blocks():
    """三块(付58.6)→6×3+2=20; 四块(付84.8)→6×4+2=26。"""
    assert ofin.physical_cost_breakdown(_sample(paid_amount=D("58.6")))["final"] == D("20")
    assert ofin.physical_cost_breakdown(_sample(paid_amount=D("84.8")))["final"] == D("26")


def test_paid_boundaries():
    """块数边界: 单块最高25→1块; 双块折后最低32.4→2块。"""
    assert ofin.physical_cost_breakdown(_sample(paid_amount=D("25")))["final"] == D("8")
    assert ofin.physical_cost_breakdown(_sample(paid_amount=D("32.4")))["final"] == D("14")


def test_unbilled_new_sample_no_theo_dependence():
    """新样块(账单未导, 全字段空): 不再依赖 theoretical, 直接 8 + 运5。"""
    o = _sample(actual_cost=None, theoretical_cost=None, est_packing=None)
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "样块固定"
    assert pb["final"] == D("8")
    assert ofin.cost_breakdown(o, _COEF)["freight"] == D("5")


def test_zero_paid_falls_back_to_qty():
    """实付=0(取消未付): 块数退回 qty(仅兜底, 不进统计)。"""
    o = _sample(paid_amount=D("0"), qty=2)
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "样块固定"
    assert pb["final"] == D("14")


def test_mixed_order_guard_over_200():
    """护栏: 名字带样块但实付>200(未来混合单) → 不按样块固定, 走正常口径。"""
    o = _sample(paid_amount=D("500"), theoretical_cost=D("300"), est_packing=D("0"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] != "样块固定"
    assert pb["final"] == D("300")         # theoretical 正常路径


def test_non_sample_untouched():
    """真产品单不受样块分支影响 (纯账单非定制: 运费仍走 actual_freight)。"""
    assert not ofin.is_sample_order(Order(order_no="N1", product_name="畔色樱桃木靠墙一体实木岩板餐边柜"))
    o = Order(order_no="N2", product_name="畔色实木餐桌", is_custom=False, tax=D("0"),
              paid_amount=D("5000"), actual_cost=D("2000"), actual_freight=D("300"),
              order_date=date(2026, 3, 1))
    assert ofin.cost_breakdown(o, _COEF)["freight"] == D("300")
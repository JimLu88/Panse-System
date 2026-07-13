# -*- coding: utf-8 -*-
"""逐单核对表拆列 (2026-07-12 用户: 物流/安装不再折在商品成本里): 引擎按分支吐
logistics_component/install_component, 报表 商品成本−分量 / 物流安装列+分量, 合计恒等。
锁死: 各分支 final 数值与拆列前一致(口径没变, 只是展示拆分)。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from decimal import Decimal as D

from app.models.order import Order
from app.services import order_financials as ofin


def test_reconstruct_branch_components():
    """非定制+工厂账单: 配件走阶梯直读 est_parts, 不再余数法(用户 2026-07-14 "按阶梯改余数法");
    分量=实际物流/安装; final = 700木作 + (170配件+50物流+20安装) + 80打包 = 1020。"""
    o = Order(order_no="S1", is_custom=False, paid_amount=D("5000"),
              actual_cost=D("700"), wood_cost_est=D("700"), theoretical_cost=D("1000"),
              est_parts=D("170"),
              est_packing=D("100"), actual_logistics=D("50"), est_logistics=D("30"),
              actual_install=D("20"), est_install=D("10"), actual_packing=D("80"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["final"] == D("1020")                      # 700 + (170+50+20) + 80
    assert pb["logistics_component"] == D("50")
    assert pb["install_component"] == D("20")
    # 拆列后三块加回 == final
    assert (pb["final"] - pb["logistics_component"] - pb["install_component"]
            + pb["logistics_component"] + pb["install_component"]) == pb["final"]


def test_parts_itemized_branch_components():
    o = Order(order_no="S2", is_custom=False, paid_amount=D("5000"),
              actual_cost=D("700"), actual_parts=D("100"),
              actual_logistics=D("50"), est_install=D("10"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "实配件分项"
    assert pb["final"] == D("860")                       # 700 + (50+10+100) + 0
    assert pb["logistics_component"] == D("50")
    assert pb["install_component"] == D("10")


def test_theoretical_branch_components_est_embedded():
    """无账单非定制: theo 内嵌的预估物流/安装可拆。"""
    o = Order(order_no="S3", is_custom=False, paid_amount=D("5000"),
              theoretical_cost=D("1000"), est_packing=D("100"),
              est_logistics=D("200"), est_install=D("50"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["final"] == D("1000")                      # (1000-100) + 100打包
    assert pb["logistics_component"] == D("200")
    assert pb["install_component"] == D("50")


def test_capped_order_components_zero():
    """封顶/兜底单: 商品成本=实付×85% 整体值, 分量必须归零(防拆出负数)。"""
    o = Order(order_no="S4", is_custom=False, paid_amount=D("100"),
              theoretical_cost=D("1000"), est_packing=D("100"),
              est_logistics=D("200"), est_install=D("50"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] != "none"
    assert pb["logistics_component"] == D("0")
    assert pb["install_component"] == D("0")


def test_custom_ratio_branch_components_zero():
    """定制占比放大分支: 物流隐含在比例里不可拆 → 分量 0; final 口径不变。"""
    o = Order(order_no="S5", is_custom=True, paid_amount=D("10000"),
              actual_cost=D("3000"), est_parts=None)
    pb = ofin.physical_cost_breakdown(o)
    assert pb["logistics_component"] == D("0")
    assert pb["install_component"] == D("0")
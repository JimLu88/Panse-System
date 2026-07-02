# -*- coding: utf-8 -*-
"""第一阶段两表统一 (2026-07-02, 用户拍板): 定制单核对表的展示成本 = 主单核对表
(逐单核对表 order_financials.physical_cost_breakdown) —— 同一函数、同一个数, 天然一致。
不再用规则链 r.cost(那会与逐单核对表不一致, 如 surcharge 双算)。
(o 只作内存对象传入 _row/physical_cost_breakdown, 无需落库。)"""
from decimal import Decimal

from app.models.order import Order
from app.services import custom_order_reconcile_service as cust
from app.services import order_financials as ofin


def test_row_cost_equals_main_breakdown_not_rule(db_session):
    """有 custom_surcharge 的定制单: 规则链 r.cost=4300(双算), 但 _row 展示 = 主口径(≈2898)。"""
    o = Order(order_no="U1", is_custom=True, paid_amount=Decimal("3409.10"),
              theoretical_cost=Decimal("2150"), custom_surcharge=Decimal("2150"),
              est_packing=Decimal("170"), remark="定制尺寸1800×750")
    r = cust._display_resolve(db_session, o, cust.remark_text(o))
    row = cust._row(db_session, o, r)
    bd = ofin.physical_cost_breakdown(o)
    assert row["projected_cost"] == float(bd["final"])      # 与逐单核对表同一个数
    assert row["projected_cost"] != float(r["cost"])        # 不是规则链的 4300 双算
    assert abs(row["projected_cost"] - 2897.74) < 0.5       # 主口径 定制兜底85 = 实付×85%


def test_row_marks_review_on_floor(db_session):
    """主口径走兜底/封顶(粗估) 且无工厂实报 → needs_review 标红, method=主口径封顶模式。"""
    o = Order(order_no="U2", is_custom=True, paid_amount=Decimal("3409.10"),
              theoretical_cost=Decimal("2150"), est_packing=Decimal("170"), remark="定制尺寸1800×750")
    row = cust._row(db_session, o, {"source": "x"})
    assert row["needs_review"] is True
    assert row["method"] == "定制兜底85"


def test_row_actual_cost_high_confidence(db_session):
    """已填工厂实报 → 高置信, 不标红。"""
    o = Order(order_no="U3", is_custom=True, paid_amount=Decimal("3000"),
              actual_cost=Decimal("1800"), remark="定制")
    row = cust._row(db_session, o, {"source": "factory"})
    assert row["needs_review"] is False
    assert row["confidence"] == "high"
    assert row["actual_cost"] == 1800.0


def test_row_socket_matches_per_order(db_session):
    """专链+插座单: 定制单核对表 = 逐单核对表(都走主口径的专链插座追加=118)。"""
    o = Order(order_no="U4", is_custom=True, product_name="畔色木作 差价邮费补拍专链",
              paid_amount=Decimal("199.76"), theoretical_cost=Decimal("118"), remark="两个T25插座")
    row = cust._row(db_session, o, {"source": "socket"})
    bd = ofin.physical_cost_breakdown(o)
    assert row["projected_cost"] == float(bd["final"])   # 两表一致
    assert abs(row["projected_cost"] - 118.0) < 0.01

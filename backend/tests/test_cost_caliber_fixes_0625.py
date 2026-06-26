# -*- coding: utf-8 -*-
"""2026-06-25 成本/利润口径修复回归:
A. 定制单工厂账单缺配件参照(theoretical≤wood) → 物理成本 = max(组件, 实付×85%) (只升不降; 片段仍封顶)。
B. ROI compute() 营收 = 实付 − 退款 (原先只 Σ实付, 虚高)。
D4. 工厂账单回填 backfill_order_actual_cost 覆盖重算(分批开票不丢, 幂等)。
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.factory_recon_item import FactoryReconItem
from app.models.marketing import PromotionFlow
from app.models.order import Order
from app.services import factory_recon_import_service as fr
from app.services import order_financials as ofin
from app.services import roi_service


# ───────── A. 定制单缺配件 → 85% 兜底 (用户 2026-06-25: 工厂价优先, 缺配件就全按85%) ─────────

def test_custom_missing_parts_floors_to_85pct():
    """定制单有工厂木作账单(170)但无定价表配件参照(theoretical≤wood) → max(组件170, 实付2000×85%=1700)=1700。"""
    o = Order(order_no="C1", is_custom=True, paid_amount=Decimal("2000"),
              actual_cost=Decimal("170"), wood_cost_est=Decimal("3000"),
              theoretical_cost=Decimal("170"))
    assert ofin.physical_cost(o) == Decimal("1700.00")


def test_custom_full_bill_uses_wood_ratio():
    """定制单账单合理且【高于实付×85%floor】: 物理成本 = 木作账单÷木作占比(默认0.67) = 2400/0.67 ≈ 3582,
    保留真实推算 (floor 只升不降, 实付×85%=3400 低于推算 → 不抬高也不压低)。"""
    o = Order(order_no="C2a", is_custom=True, paid_amount=Decimal("4000"), actual_cost=Decimal("2400"))
    phys = ofin.physical_cost(o)
    assert abs(phys - Decimal("3582")) < Decimal("2")
    assert Decimal("3400") < phys < Decimal("4000")   # 高于85%floor → 不被floor抬高, 也未超实付


def test_custom_partial_bill_falls_back_85():
    """定制单工厂账单像零头(账单1000 vs 实付5000, 推算毛利70%>30%阈值) → 退回 实付×85% = 4250。"""
    o = Order(order_no="C2b", is_custom=True, paid_amount=Decimal("5000"),
              actual_cost=Decimal("1000"), wood_cost_est=Decimal("800"), theoretical_cost=Decimal("2000"))
    assert ofin.physical_cost(o) == Decimal("4250.00")


def test_custom_fragment_still_capped():
    """定制定金片段(实付100 远小于组件583) → 仍片段封顶 实付×85%=85, 不被floor抬高。"""
    o = Order(order_no="C3", is_custom=True, paid_amount=Decimal("100"),
              actual_cost=Decimal("63"), wood_cost_est=Decimal("3000"),
              theoretical_cost=Decimal("63"),
              est_logistics=Decimal("350"), est_packing=Decimal("170"))
    assert ofin.physical_cost(o) == Decimal("85.00")


def test_noncustom_not_floored():
    """非定制单不受85% floor影响(规则仅针对定制单): 组件170 保持170。"""
    o = Order(order_no="N1", is_custom=False, sku_code="PPS001", paid_amount=Decimal("2000"),
              actual_cost=Decimal("170"), wood_cost_est=Decimal("0"), theoretical_cost=None)
    assert ofin.physical_cost(o) == Decimal("170")


# ───────── B. ROI 营收减退款 ─────────

def test_roi_compute_subtracts_refund(db_session):
    db_session.add(PromotionFlow(flow_type="支出", amount=Decimal("1000"), transaction_date=date(2026, 6, 1)))
    db_session.add(Order(platform="淘宝", order_no="R1", status="paid", is_refill=False,
                         order_date=date(2026, 6, 5), paid_amount=Decimal("10000")))
    db_session.add(Order(platform="淘宝", order_no="R2", status="paid", is_refill=False,
                         order_date=date(2026, 6, 6), paid_amount=Decimal("10000"),
                         refund_amount=Decimal("2000")))
    db_session.flush()
    r = roi_service.compute(db_session)
    # 营收 = 10000 + (10000 − 2000) = 18000 (减了退款, 非 20000)
    assert r.order_revenue == Decimal("18000")


# ───────── D4. 工厂账单回填覆盖重算 ─────────

def test_backfill_overwrites_stale_actual_cost(db_session):
    """已有(过期)actual_cost=100 的单, 来了账单行500 → 覆盖为500(原先"仅填空"会保留100)。"""
    db_session.add(Order(platform="淘宝", order_no="F1", status="paid", paid_amount=Decimal("6000"),
                         actual_cost=Decimal("100")))
    db_session.add(FactoryReconItem(order_no="F1", settle_price=Decimal("500")))
    db_session.flush()
    fr.backfill_order_actual_cost(db_session, restrict_to={"F1"})
    o = db_session.execute(select(Order).where(Order.order_no == "F1")).scalar_one()
    assert o.actual_cost == Decimal("500")


def test_backfill_sums_multi_batch(db_session):
    """分批开票: 同单两行(300+200) → actual_cost=500 (不丢第2批)。"""
    db_session.add(Order(platform="淘宝", order_no="F2", status="paid", paid_amount=Decimal("6000")))
    db_session.add(FactoryReconItem(order_no="F2", settle_price=Decimal("300")))
    db_session.add(FactoryReconItem(order_no="F2", settle_price=Decimal("200")))
    db_session.flush()
    fr.backfill_order_actual_cost(db_session, restrict_to={"F2"})
    o = db_session.execute(select(Order).where(Order.order_no == "F2")).scalar_one()
    assert o.actual_cost == Decimal("500")


# ───────── E. 推演单(无工厂账单)成本>实付 → 实付×85% 封顶 (用户 2026-06-25 选A) ─────────

def test_estimate_over_paid_caps_to_85():
    """无工厂账单(全推演), 推演物理成本(467.5定价+170打包=637.5) > 实付550 → 封顶 550×0.85=467.5。"""
    o = Order(order_no="E1", actual_cost=None, paid_amount=Decimal("550"),
              theoretical_cost=Decimal("467.5"), est_packing=Decimal("170"))
    assert ofin.physical_cost(o) == Decimal("467.50")


def test_estimate_under_paid_unchanged():
    """推演成本(300) ≤ 实付(550) → 不封顶, 保持 300。"""
    o = Order(order_no="E2", actual_cost=None, paid_amount=Decimal("550"),
              theoretical_cost=Decimal("300"))
    assert ofin.physical_cost(o) == Decimal("300")


def test_actual_bill_over_paid_not_capped_by_estimate_rule():
    """有工厂账单 actual_cost=600 > 实付550 → 不被推演封顶规则改(以账单为准, 仅受50%片段封顶约束)。"""
    o = Order(order_no="E3", is_custom=False, sku_code="PPS900", actual_cost=Decimal("600"),
              wood_cost_est=Decimal("0"), theoretical_cost=None, paid_amount=Decimal("550"))
    assert ofin.physical_cost(o) == Decimal("600")


# ───────── F. physical_cost_breakdown 拆解自洽 (导出公式回推依赖此) ─────────

def test_breakdown_estimate_reconciles():
    """推演单: 估算已不含嵌入打包(=theoretical−est_pack), final = 0 + 估算 + 打包(一次)。(2026-06-26 打包修复)"""
    o = Order(order_no="B1", actual_cost=None, paid_amount=Decimal("5000"),
              theoretical_cost=Decimal("2000"), est_packing=Decimal("100"))
    b = ofin.physical_cost_breakdown(o)
    assert b["cap_mode"] == "none"
    assert b["factory_wood"] == Decimal("0")
    assert b["estimate_part"] == Decimal("1900")   # 2000 − 嵌入打包100
    assert b["packing"] == Decimal("100")
    assert b["precap_total"] == Decimal("2000")
    assert b["final"] == Decimal("2000") == ofin.physical_cost(o)


def test_breakdown_actual_reconstruct_reconciles():
    """有账单可还原: final = 木作账单 + (定价表物理−木作−嵌入打包) + 打包(一次)。(2026-06-26 打包修复)"""
    o = Order(order_no="B2", actual_cost=Decimal("1000"), wood_cost_est=Decimal("800"),
              theoretical_cost=Decimal("2000"), paid_amount=Decimal("5000"), est_packing=Decimal("50"))
    b = ofin.physical_cost_breakdown(o)
    assert b["factory_wood"] == Decimal("1000")
    assert b["estimate_part"] == Decimal("1150")     # 2000 − 800 − 嵌入打包50
    assert b["packing"] == Decimal("50")
    assert b["cap_mode"] == "none"
    assert b["factory_wood"] + b["estimate_part"] + b["packing"] == b["precap_total"]
    assert b["final"] == Decimal("2200") == ofin.physical_cost(o)


def test_breakdown_cap_flags_final_equals_physical_cost():
    """各封顶模式: final 始终等于 physical_cost; 推演封顶/片段 标记正确。(2026-06-26: 打包不再虚增 precap)"""
    cap = Order(order_no="B3", actual_cost=None, paid_amount=Decimal("550"),
                theoretical_cost=Decimal("800"), est_packing=Decimal("170"))
    b = ofin.physical_cost_breakdown(cap)
    assert b["precap_total"] == Decimal("800")   # 估算(800−170) + 打包170
    assert b["cap_mode"] == "推演封顶85"
    assert b["final"] == Decimal("467.50") == ofin.physical_cost(cap)


def test_breakdown_negative_precap_zeroed():
    """加法分量算出负数(物流实际远小于预估) → 归零, 标记 归零; final=0=physical_cost。"""
    o = Order(order_no="Z1", actual_cost=None, paid_amount=Decimal("1000"),
              theoretical_cost=Decimal("100"), actual_logistics=Decimal("0"), est_logistics=Decimal("500"))
    b = ofin.physical_cost_breakdown(o)
    assert b["precap_total"] == Decimal("-400")
    assert b["cap_mode"] == "归零"
    assert b["final"] == Decimal("0") == ofin.physical_cost(o)

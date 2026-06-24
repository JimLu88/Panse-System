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


def test_custom_reconstructable_unchanged():
    """定制单有定价表参照(theoretical>wood) → 正常补非木作(1000+1200=2200), 不走85% floor。"""
    o = Order(order_no="C2", is_custom=True, paid_amount=Decimal("5000"),
              actual_cost=Decimal("1000"), wood_cost_est=Decimal("800"),
              theoretical_cost=Decimal("2000"))
    assert ofin.physical_cost(o) == Decimal("2200")


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

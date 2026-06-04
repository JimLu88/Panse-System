"""测试补全的 8 个缺口: A-H.

A. 仓库回填 (订单导入 + backfill_warehouse)
B. 订单细节带工厂订单号
C. 非订单售后流水自动建售后表
D. 退款对识别
E. 工厂流水按账单金额匹配
F. 物料名相似度猜测 (可选: 需要 Material 记录)
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.finance import AlipayFlow, FactoryReconciliation
from app.models.marketing import AfterSales
from app.models.order import FactoryOrder, Order, OrderDetail
from app.services import (
    alipay_flow_router_service,
    factory_reconciliation_service,
    flow_refund_service,
    order_cost_service,
    order_detail_service,
    order_sync_service,
)


# ─────────────────────────────── helpers ──────────────────────────────────────

def _order(db, no, product_name=None, sku=None, is_refill=False, warehouse=None):
    o = Order(
        platform="淘宝", order_no=no, is_refill=is_refill,
        product_name=product_name, sku=sku,
        warehouse=warehouse,
    )
    db.add(o)
    db.flush()
    return o


def _factory_order(db, no, platform_no, factory_no):
    fo = FactoryOrder(
        factory_order_no=factory_no,
        platform_order_no=platform_no,
        order_date=date(2026, 3, 1),
        factory_name="博冠家具",
        factory_bill_amount=Decimal("500"),
        expected_amount=Decimal("500"),
        qty=1,
    )
    db.add(fo)
    db.flush()
    return fo


def _alipay(db, no, amount, when=None, related=None, recon_type=None, account="企业号"):
    f = AlipayFlow(
        account=account,
        transaction_no=no,
        amount=Decimal(str(amount)),
        transaction_time=when or datetime(2026, 3, 10),
        related_order_no=related,
        reconciliation_type=recon_type,
    )
    db.add(f)
    db.flush()
    return f


# ─────────────────────────────── Gap A ───────────────────────────────────────
# 仓库回填

def test_default_warehouse_for_refill():
    """补单/样块订单 → 杭州."""
    assert order_cost_service.default_warehouse_for("普通产品", "普通SKU", True) == "杭州"


def test_default_warehouse_for_sample():
    """产品名含「样块」→ 杭州."""
    assert order_cost_service.default_warehouse_for("PPS样块测试", None, False) == "杭州"


def test_default_warehouse_for_normal():
    """普通订单 → 江西仓库."""
    assert order_cost_service.default_warehouse_for("PPS2633", "标准", False) == "江西仓库"


def test_backfill_warehouse(db_session):
    """存量 warehouse=None 订单被自动填充。"""
    o1 = _order(db_session, "W001", product_name="样块测试", is_refill=False, warehouse=None)
    o2 = _order(db_session, "W002", product_name="普通品", is_refill=True, warehouse=None)
    o3 = _order(db_session, "W003", product_name="普通品", warehouse="已填")  # 不应被改动

    n = order_sync_service.backfill_warehouse(db_session)
    assert n == 2
    db_session.refresh(o1)
    db_session.refresh(o2)
    db_session.refresh(o3)
    assert o1.warehouse == "杭州"      # 样块
    assert o2.warehouse == "杭州"      # 补单
    assert o3.warehouse == "已填"      # 保持不变


# ─────────────────────────────── Gap B ───────────────────────────────────────
# 订单细节带工厂订单号

def test_order_detail_includes_factory_order_no(db_session):
    """生成订单细节时工厂订单号被带入。"""
    from app.models.bom import BomLine

    # 建 Order
    o = _order(db_session, "TBK001", product_name="测试产品")
    o.product_code = "P001"
    o.sku_code = None
    db_session.flush()

    # 建 BomLine
    db_session.add(BomLine(
        product_code="P001",
        material_code="AC-001",
        material_name="螺丝",
        qty_per_product=Decimal("2"),
    ))
    db_session.flush()

    # 建 FactoryOrder 关联
    _factory_order(db_session, "FO001", platform_no="TBK001", factory_no="PANSE2026001")

    report = order_detail_service.generate(db_session, order_nos=["TBK001"])
    assert report.details_created >= 1

    detail = db_session.query(OrderDetail).filter_by(order_no="TBK001").first()
    assert detail is not None
    assert detail.factory_order_no == "PANSE2026001"


# ─────────────────────────────── Gap C ───────────────────────────────────────
# 非订单售后流水自动建售后表

def test_create_aftersales_from_flows(db_session):
    """amount<0 + related_order_no + 售后类备注 → 建售后记录。"""
    # 普通售后备注流水
    _alipay(db_session, "AS001", -88, related="TBK999", recon_type=None)
    # 添加备注标识售后
    f = db_session.query(AlipayFlow).filter_by(transaction_no="AS001").one()
    f.remark = "买家退款-售后处理"
    db_session.flush()

    n = alipay_flow_router_service.create_aftersales_from_flows(db_session)
    assert n == 1

    rec = db_session.query(AfterSales).filter_by(platform_order_no="TBK999").first()
    assert rec is not None
    assert rec.alipay_flow_no == "AS001"
    assert rec.direct_compensation == Decimal("88")


def test_aftersales_flow_not_taken_by_purchases(db_session):
    """已建售后记录的流水号不应再被 create_purchases 抢走。"""
    _alipay(db_session, "AS002", -100, related="TBK998")
    f = db_session.query(AlipayFlow).filter_by(transaction_no="AS002").one()
    f.remark = "退款"
    db_session.flush()

    alipay_flow_router_service.create_aftersales_from_flows(db_session)
    purchases_created = alipay_flow_router_service.create_purchases_from_unclassified(db_session)

    from app.models.order import PartPurchase
    pp = db_session.query(PartPurchase).filter_by(alipay_flow_no="AS002").first()
    assert pp is None, "售后流水不应被采购抢走"


# ─────────────────────────────── Gap D ───────────────────────────────────────
# 退款对识别

def test_detect_refunds_pairs_opposite_amounts(db_session):
    """同 related_order_no 下金额相等方向相反的流水被标为退款对。"""
    _alipay(db_session, "RF_IN", 200, related="TBK777")
    _alipay(db_session, "RF_OUT", -200, related="TBK777")
    db_session.flush()

    n = flow_refund_service.detect_refunds(db_session)
    assert n == 1

    inc = db_session.query(AlipayFlow).filter_by(transaction_no="RF_IN").one()
    exp = db_session.query(AlipayFlow).filter_by(transaction_no="RF_OUT").one()
    assert inc.reconciliation_type == "refund_in"
    assert exp.reconciliation_type == "refund_out"
    assert inc.reconciliation_status == "matched"
    assert exp.reconciliation_status == "matched"


def test_detect_refunds_no_false_positive(db_session):
    """金额不相等的流水不应被配对。"""
    _alipay(db_session, "NRF_IN", 200, related="TBK888")
    _alipay(db_session, "NRF_OUT", -150, related="TBK888")
    db_session.flush()

    n = flow_refund_service.detect_refunds(db_session)
    assert n == 0


# ─────────────────────────────── Gap E ───────────────────────────────────────
# 工厂流水按账单金额匹配

def test_factory_alipay_bill_amount_match(db_session):
    """工厂账单合计 500 元, 找到等额支出流水, 回填工厂订单 alipay_flow_no。"""
    fo = FactoryOrder(
        factory_order_no="PANSE20260301",
        order_date=date(2026, 3, 1),
        factory_name="博冠家具",
        factory_bill_amount=Decimal("500"),
        expected_amount=Decimal("500"),
        qty=1,
    )
    db_session.add(fo)
    db_session.flush()

    # 支出流水 500 元
    _alipay(db_session, "FAP001", -500, related=None)
    f = db_session.query(AlipayFlow).filter_by(transaction_no="FAP001").one()
    f.counterparty = "博冠家具"
    db_session.flush()

    n = factory_reconciliation_service.match_factory_alipay_by_bill_amount(db_session)
    assert n == 1

    db_session.refresh(fo)
    assert fo.alipay_flow_no == "FAP001"

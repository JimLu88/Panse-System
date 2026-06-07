# -*- coding: utf-8 -*-
"""逐笔对账 + 到账覆盖缺口诊断测试。"""
from datetime import date
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.models.order import Order
from app.models.settlement import OrderSettlement
from app.services import order_reconciliation_service as R


def _order(db, no, d, payable, *, flow_no=None):
    db.add(Order(platform="淘宝", order_no=no, order_date=d,
                 buyer_payable_amount=Decimal(str(payable)), alipay_flow_no=flow_no,
                 status="signed"))


def test_coverage_gap_by_month(db_session):
    db = db_session
    # 2026-04: O1 有支付宝到账证据, O2 无证据
    _order(db, "O1", date(2026, 4, 5), 1000, flow_no="T1")
    db.add(AlipayFlow(account="企业号", transaction_no="T1", amount=Decimal("960"), balance=Decimal("960")))
    _order(db, "O2", date(2026, 4, 9), 2000)
    # 2026-05: O3 无证据
    _order(db, "O3", date(2026, 5, 1), 3000)
    db.flush()

    g = R.coverage_gap(db)
    assert g["total_orders"] == 3
    assert g["evidence_orders"] == 1
    assert g["pending_orders"] == 2
    assert g["coverage_pct"] == round(1 / 3 * 100, 1)

    by = {m["period"]: m for m in g["months"]}
    assert by["2026-04"]["orders"] == 2 and by["2026-04"]["evidence"] == 1
    assert by["2026-04"]["coverage_pct"] == 50.0
    assert by["2026-05"]["coverage_pct"] == 0.0
    # 待补金额 = 应付 - 2%税 - 软件费: O2=1960, O3=2940
    assert by["2026-04"]["pending_amount"] == 1960.0
    assert by["2026-05"]["pending_amount"] == 2940.0
    # 缺口最大的月在前 (5月待补 2940 > 4月 1960)
    assert g["worst_months"][0] == "2026-05"


def test_coverage_gap_wechat_evidence(db_session):
    db = db_session
    _order(db, "W1", date(2026, 5, 2), 500)
    db.add(OrderSettlement(source="wechat", pay_no="P1", order_no="W1",
                           income=Decimal("480"), expense=Decimal("0")))
    db.flush()
    g = R.coverage_gap(db)
    assert g["evidence_orders"] == 1 and g["pending_orders"] == 0
    assert g["months"][0]["wechat"] == 1

# -*- coding: utf-8 -*-
"""退款不被误归工厂货款 + 退款对账认 refund 家族(根治流水19365"山**"→"玉山"假匹配日更覆盖)。"""
from datetime import date, datetime
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.models.factory_settlement import FactorySupplierAlias
from app.models.order import Order
from app.services import factory_settlement_service as fss
from app.services import reconciliation_service as rec


def _flow(**kw):
    kw.setdefault("balance", Decimal("0"))
    return AlipayFlow(**kw)


def test_route_skips_customer_refund_even_if_name_matches_alias(db_session):
    """给客户「山**」的退款: 去星号「山」子串命中「玉山」别名, 但退款护栏拦下, 不改 factory_payment。"""
    db_session.add(FactorySupplierAlias(supplier="玉山县博冠家具", alias="玉山", note="t"))
    db_session.add(_flow(account="个体户私账", transaction_no="REF19365", transaction_type="退款",
                         amount=Decimal("-156.55"), counterparty="山**", remark="退款-畔色餐边柜",
                         reconciliation_type="refund", related_order_no="4990013425203542801",
                         transaction_time=datetime(2026, 2, 28, 1, 41)))
    db_session.commit()
    fss.route_alipay_settlements(db_session)
    f = db_session.query(AlipayFlow).filter_by(transaction_no="REF19365").one()
    assert f.reconciliation_type == "refund"          # 没被日更改成 factory_payment


def test_route_still_classifies_real_factory_payment(db_session):
    """对照: 真·工厂货款(玉山货款结算)仍应归 factory_payment, 护栏不误伤。"""
    db_session.add(FactorySupplierAlias(supplier="玉山县博冠家具", alias="玉山", note="t"))
    db_session.add(_flow(account="主力号", transaction_no="FAC1", transaction_type="转账",
                         amount=Decimal("-5000"), counterparty="玉山县博冠家具", remark="6月货款结算",
                         reconciliation_type=None, transaction_time=datetime(2026, 6, 30, 10, 0)))
    db_session.commit()
    fss.route_alipay_settlements(db_session)
    f = db_session.query(AlipayFlow).filter_by(transaction_no="FAC1").one()
    assert f.reconciliation_type == "factory_payment"


def test_refund_recon_counts_refund_type(db_session):
    """退款对账: reconciliation_type='refund' 的退款流出也计入"支付宝实退"(原来只认 refund_out → 假报实退0)。"""
    db_session.add(Order(platform="淘宝", order_no="RR1", refund_amount=Decimal("156.55"),
                         refund_date=date(2026, 2, 28)))
    db_session.add(_flow(account="个体户私账", transaction_no="RRFLOW1", transaction_type="退款",
                         amount=Decimal("-156.55"), reconciliation_type="refund",
                         transaction_time=datetime(2026, 2, 28, 10, 0)))
    db_session.commit()
    res = rec.run_refund_reconciliation(db_session, record_exceptions=False)
    feb = next((d for d in res.diffs if d.key == "2026-02"), None)
    assert feb is not None
    assert abs(Decimal(str(feb.actual)) - Decimal("156.55")) < Decimal("0.01")   # 实退计入
    assert feb.severity == "ok"                             # 应退=实退 → 平

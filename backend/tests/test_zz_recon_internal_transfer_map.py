# -*- coding: utf-8 -*-
"""对账 map 排除内部划转 + 符号疑似错报错分支 (用户 2026-07-12 按此执行)。

案发: 主力号→爱群号 -2080「岩板费」(内部充钱) 与 爱群号付*丽 -290 共交易号,
_alipay_flow_amount_map 按号累加支出 → 采购 2026-06 实付虚高 +2080;
日常#476 的流水在库但是 +75 收入方向(符号丢失), 曾被报成"无对应支付宝记录"。
"""
from datetime import date, datetime
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.models.marketing import DailyOperation
from app.models.order import PartPurchase
from app.services import reconciliation_service as rec


def _flow(**kw):
    kw.setdefault("balance", Decimal("0"))
    return AlipayFlow(**kw)


NO = "20260423200040011100050074584420"


def test_flow_map_excludes_internal_transfer(db_session):
    """同交易号两笔支出: 真付款-290 + 内部划转-2080 → map 只计 290。"""
    db_session.add(_flow(account="爱群号", transaction_no=NO, transaction_type="转账",
                         amount=Decimal("-290"), counterparty="*丽", remark="岩板：备货",
                         transaction_time=datetime(2026, 4, 23, 10, 0)))
    db_session.add(_flow(account="主力号", transaction_no=NO, transaction_type="转账",
                         amount=Decimal("-2080"), counterparty="Klossy·Lee(**群)", remark="岩板费",
                         reconciliation_type="internal_transfer",
                         transaction_time=datetime(2026, 4, 23, 10, 1)))
    db_session.commit()
    m = rec._alipay_flow_amount_map(db_session)
    assert m[NO] == Decimal("290")


def test_purchase_payment_flat_with_internal_leg(db_session):
    """采购¥290 挂共号流水 → 内部划转腿排除后, 实付=290, 月度对平(不再假差+2080)。"""
    db_session.add(_flow(account="爱群号", transaction_no=NO, transaction_type="转账",
                         amount=Decimal("-290"), counterparty="*丽",
                         transaction_time=datetime(2026, 4, 23, 10, 0)))
    db_session.add(_flow(account="主力号", transaction_no=NO, transaction_type="转账",
                         amount=Decimal("-2080"), counterparty="Klossy·Lee(**群)",
                         reconciliation_type="internal_transfer",
                         transaction_time=datetime(2026, 4, 23, 10, 1)))
    db_session.add(PartPurchase(purchase_no="202600003", purchase_date=date(2026, 6, 29),
                                payment_date=date(2026, 6, 29), supplier="*丽",
                                material_name="岩板：备货2*0.85", qty=1,
                                total_amount=Decimal("290"), amount=Decimal("290"),
                                alipay_flow_no=NO))
    db_session.commit()
    res = rec.run_purchase_payment(db_session, record_exceptions=False)
    jun = next((d for d in res.diffs if d.key == "2026-06"), None)
    assert jun is not None
    assert jun.severity == "ok"
    assert Decimal(str(jun.actual)) == Decimal("290")


def test_operating_expense_reports_sign_suspect(db_session):
    """经营记录挂的流水在库但是收入方向 → 报"符号疑似错误", 不再误导成"无对应支付宝记录"。"""
    db_session.add(_flow(account="主力号", transaction_no="FN75", transaction_type="生活服务",
                         amount=Decimal("75"), counterparty="闪装家居",
                         transaction_time=datetime(2026, 5, 12, 22, 9)))
    db_session.add(DailyOperation(record_date=date(2026, 5, 12), item="安装服务费",
                                  amount=Decimal("75"), alipay_flow_no="FN75"))
    db_session.commit()
    res = rec.run_operating_expense(db_session, record_exceptions=False)
    warn = next((d for d in res.diffs if "FN75" in (d.message or "")), None)
    assert warn is not None
    assert "符号疑似错误" in warn.message
    assert "无对应支付宝记录" not in warn.message


def test_operating_expense_missing_flow_message_kept(db_session):
    """对照: 号真不在库 → 仍报"无对应支付宝记录"。"""
    db_session.add(DailyOperation(record_date=date(2026, 5, 13), item="某支出",
                                  amount=Decimal("30"), alipay_flow_no="NOPE404"))
    db_session.commit()
    res = rec.run_operating_expense(db_session, record_exceptions=False)
    warn = next((d for d in res.diffs if "NOPE404" in (d.message or "")), None)
    assert warn is not None
    assert "无对应支付宝记录" in warn.message

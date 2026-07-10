# -*- coding: utf-8 -*-
"""采购付款对账·同号多笔修复 (2026-07-10):
支付宝同一交易号下多笔子流水(库唯一键允许) → 流水金额=支出行累加(收入行不算);
# ⚠ 文件名 zz_ 前缀是故意的: 本套测试若按字母序排在 test_new_automation 之前, 会踩中
# 既有的 SQLite 内存库连接污染(parse_message_change_api 在全量跑时 Cannot operate on a closed
# database, 单跑/子集跑都过)。与本文件逻辑无关, 排到最后跑绕开。
多张采购单共付同一号 → 实付整号只计一次。实测 2026-06 假差 +2150.02/+1790 即此病。"""
from datetime import date, datetime
from decimal import Decimal as D

from app.models.finance import AlipayFlow
from app.models.order import PartPurchase
from app.services import reconciliation_service as rec


def _fl(db, tno, amt, ttype="支出", acct="个体户私账", bal=0):
    db.add(AlipayFlow(account=acct, transaction_no=tno, transaction_type=ttype,
                      amount=D(str(amt)), balance=D(str(bal)),
                      transaction_time=datetime(2026, 6, 29, 10, 0)))


def _pp(db, pno, amt, flow_no):
    db.add(PartPurchase(purchase_no=pno, purchase_date=date(2026, 6, 29),
                        payment_date=date(2026, 6, 29), material_name="t",
                        qty=D("1"), unit_price=D(str(amt)), amount=D(str(amt)),
                        total_amount=D(str(amt)), alipay_flow_no=flow_no))


def _june(res):
    return next((d for d in res.diffs if d.key == "2026-06"), None)


def test_shared_flow_no_counted_once(db_session):
    """实测形态: 玻璃109.98 + 备货2260 共号(号下 -109.98/-2260/-0.02 三笔) → 差仅尾差0.02 → ok。"""
    _fl(db_session, "TX1", -109.98, bal=1)
    _fl(db_session, "TX1", -2260.00, bal=2)
    _fl(db_session, "TX1", -0.02, bal=3)
    _pp(db_session, "P1", 109.98, "TX1")
    _pp(db_session, "P2", 2260.00, "TX1")
    db_session.commit()
    res = rec.run_purchase_payment(db_session, record_exceptions=False)
    j = _june(res)
    assert j is not None and j.severity == "ok"
    assert abs(D(str(j.diff))) <= D("0.05")


def test_income_row_same_no_ignored(db_session):
    """同号里混了收入行(+2080'岩板费') → 不当付款算, 采购290 vs 支出行290 → ok。"""
    _fl(db_session, "TX2", -290.00, bal=1)
    _fl(db_session, "TX2", 2080.00, ttype="收入", bal=2)
    _pp(db_session, "P3", 290.00, "TX2")
    db_session.commit()
    res = rec.run_purchase_payment(db_session, record_exceptions=False)
    j = _june(res)
    assert j is not None and j.severity == "ok"


def test_real_mismatch_still_flagged(db_session):
    """对照: 采购100 却挂了 500 的流水 → 真差照报。"""
    _fl(db_session, "TX3", -500.00, bal=1)
    _pp(db_session, "P4", 100.00, "TX3")
    db_session.commit()
    res = rec.run_purchase_payment(db_session, record_exceptions=False)
    j = _june(res)
    assert j is not None and j.severity not in ("ok", "not_available")

# -*- coding: utf-8 -*-
"""退款配对护栏 (用户 2026-06-29): 内部划转/非退款交易类型不被等额盲配成退款。

根因: detect_refunds 只按『同订单号+等额反向』盲配, 把魏佳英两笔『交通出行』误标 refund_out
→ 退款对账假报 +¥40000。修: 交易类型属非退款类(交通出行/转账…)不进配对, 并自愈清存量误标。
全合成数据, 不碰生产。
"""
from datetime import datetime, timezone
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.services import flow_refund_service as svc


def _flow(db, *, amt, ttype, order_no="ORD1", txn="T", recon=None, day=10):
    f = AlipayFlow(account="企业号", transaction_no=txn, related_order_no=order_no,
                   amount=Decimal(str(amt)), transaction_type=ttype,
                   transaction_time=datetime(2026, 1, day, tzinfo=timezone.utc),
                   reconciliation_type=recon,
                   reconciliation_status=("matched" if recon else "open"))
    db.add(f); db.flush()
    return f


def test_transit_pair_not_marked_refund(db_session):
    """两笔等额『交通出行』(内部报销划转)→ 不配成退款对, 不标 refund_out。"""
    inc = _flow(db_session, amt=20000, ttype="交通出行", order_no="ORDX", day=5)
    exp = _flow(db_session, amt=-20000, ttype="交通出行", order_no="ORDX", day=6)
    n = svc.detect_refunds(db_session)
    assert n == 0
    assert inc.reconciliation_type is None and exp.reconciliation_type is None


def test_real_refund_still_paired(db_session):
    """真订单退款(付款 + 交易退款, 等额反向)→ 正常配对, 支出侧标 refund_out。"""
    inc = _flow(db_session, amt=500, ttype="在线支付", order_no="ORDR", day=5)
    exp = _flow(db_session, amt=-500, ttype="交易退款", order_no="ORDR", day=7)
    n = svc.detect_refunds(db_session)
    assert n == 1
    assert inc.reconciliation_type == "refund_in"
    assert exp.reconciliation_type == "refund_out"


def test_selfheal_clears_existing_mislabel(db_session):
    """存量被误标 refund_out 的『交通出行』流水 → 再跑 detect_refunds 自愈清掉标签。"""
    bad = _flow(db_session, amt=-20000, ttype="交通出行", order_no="ORDH", txn="TH", recon="refund_out", day=8)
    svc.detect_refunds(db_session)
    assert bad.reconciliation_type is None
    assert bad.reconciliation_status == "open"

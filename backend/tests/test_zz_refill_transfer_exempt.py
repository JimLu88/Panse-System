# -*- coding: utf-8 -*-
"""刷单对账·非晶晶代付豁免 (2026-07-10): 标'非晶晶代付'的补单不进徐晶晶批次核对。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from datetime import date, datetime
from decimal import Decimal as D

from app.models.finance import AlipayFlow, RefillRecord
from app.services import reconciliation_service as rec


def _rr(db, ono, amt, comm, fee_remark="团队:水冰月"):
    db.add(RefillRecord(order_no=ono, refill_date=date(2026, 4, 15), qty=1,
                        order_amount=D(str(amt)), commission=D(str(comm)), fee_remark=fee_remark))


def _tr(db, tno, amt, remark):
    db.add(AlipayFlow(account="佳宝号", transaction_no=tno, transaction_type="转账",
                      amount=D(str(amt)), balance=D("0"), counterparty="徐晶晶", remark=remark,
                      transaction_time=datetime(2026, 4, 20, 10, 0)))


def test_non_jingjing_refill_excluded(db_session):
    """实测形态: 批次只付了常规两笔(200+15), 另一笔标'非晶晶代付'(246.38+10)另渠道 → 当日仍平。"""
    _rr(db_session, "R1", 200, 15)
    _rr(db_session, "R2", 246.38, 10, fee_remark="团队:水冰月 ｜非晶晶代付(另渠道2026-07-10已转)")
    _tr(db_session, "T1", -200, "4.15-b流水")
    _tr(db_session, "T2", -15, "4.15-Y")
    db_session.commit()
    res = rec.run_refill_transfer(db_session, record_exceptions=False)
    day = [x for x in res.diffs if "2026-04-15" in str(x.key)]
    assert day and all(x.severity == "ok" for x in day)


def test_unmarked_refill_still_counted(db_session):
    """对照: 没标豁免的补单照常计入 → 批次少付照报。"""
    _rr(db_session, "R3", 200, 15)
    _rr(db_session, "R4", 100, 10)
    _tr(db_session, "T3", -200, "4.15-b流水")
    _tr(db_session, "T4", -15, "4.15-Y")
    db_session.commit()
    res = rec.run_refill_transfer(db_session, record_exceptions=False)
    bad = [x for x in res.diffs if "2026-04-15" in str(x.key) and x.severity not in ("ok", "not_available")]
    assert bad   # 差 100/10 仍要报

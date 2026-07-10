# -*- coding: utf-8 -*-
"""企业号/主力号账面核对治本 (2026-07-10, 同推广/聚合思路).

企业号: 流水每笔带balance → 流水链自洽(锚点余额+窗口净额=窗口末笔余额), 漏导/重复分厘必现;
主力号: 流水不带balance、个人号扫码停更 → 滚动对快照, 流水未覆盖到快照日 not_available 暂缓不硬判。
"""
from datetime import date, datetime
from decimal import Decimal as D

from app.models.exception import DataException
from app.models.finance import AccountBalance, AlipayFlow
from app.services import reconciliation_service as rs


def _ab(db, name, y, m, op, cl, as_of=None):
    db.add(AccountBalance(account_name=name, period_year=y, period_month=m,
                          opening_balance=D(str(op)), closing_balance=D(str(cl)),
                          income=D("0"), expense=D("0"), as_of_date=as_of))


def _fl(db, acct, dt, amt, bal=None, tno=None):
    db.add(AlipayFlow(account=acct, transaction_no=tno or f"T{dt:%m%d%H%M%S}{amt}",
                      transaction_type="流水", amount=D(str(amt)),
                      balance=D(str(bal)) if bal is not None else None,
                      transaction_time=dt, reconciliation_status="open"))


def test_enterprise_chain_consistent_ok(db_session):
    """企业号: 锚点1000 + (+500-200) = 链尾1300 → 链自洽 ok, 不报(老①收支恒0必报)。"""
    _ab(db_session, "支付宝-企业账号", 2026, 6, op=900, cl=1000, as_of=date(2026, 6, 30))
    _ab(db_session, "支付宝-企业账号", 2026, 7, op=1000, cl=1290, as_of=date(2026, 7, 9))
    _fl(db_session, "企业号", datetime(2026, 6, 30, 10, 0), 100, bal=1000)   # 锚点
    _fl(db_session, "企业号", datetime(2026, 7, 2, 10, 0), 500, bal=1500)
    _fl(db_session, "企业号", datetime(2026, 7, 5, 10, 0), -200, bal=1300)
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=True)
    db_session.commit()
    jul = next(x for x in res.diffs if "企业" in str(x.key) and "2026-07" in str(x.key))
    assert jul.severity == "ok"
    assert not db_session.query(DataException).filter(
        DataException.status == "open", DataException.source_pk.like("%企业%2026-07%")).all()


def test_enterprise_chain_break_flags(db_session):
    """企业号: 中间漏导一笔(+500没入库) → 链尾余额对不上锚点+净额 → 报差(缺口=500)。"""
    _ab(db_session, "支付宝-企业账号", 2026, 6, op=900, cl=1000, as_of=date(2026, 6, 30))
    _ab(db_session, "支付宝-企业账号", 2026, 7, op=1000, cl=1300, as_of=date(2026, 7, 9))
    _fl(db_session, "企业号", datetime(2026, 6, 30, 10, 0), 100, bal=1000)   # 锚点
    # 漏导了 07-02 的 +500; 只导了 07-05 的 -200, 但其 balance=1300(真实链上有那笔500)
    _fl(db_session, "企业号", datetime(2026, 7, 5, 10, 0), -200, bal=1300)
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=False)
    jul = next(x for x in res.diffs if "企业" in str(x.key) and "2026-07" in str(x.key))
    assert jul.severity not in ("ok", "not_available")
    assert abs(D(str(jul.diff)) - D("500")) < D("0.01")   # 缺口精确到分


def test_main_flows_lag_not_available(db_session):
    """主力号: 流水只到07-05、快照07-10 → 暂缓 not_available, 不拿半截流水硬判假差。"""
    _ab(db_session, "主力号", 2026, 6, op=8000, cl=8000, as_of=date(2026, 6, 30))
    _ab(db_session, "主力号", 2026, 7, op=8000, cl=10250.49, as_of=date(2026, 7, 10))
    _fl(db_session, "主力号", datetime(2026, 7, 5, 10, 0), 2892.42)
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=True)
    db_session.commit()
    jul = next(x for x in res.diffs if "主力" in str(x.key) and "2026-07" in str(x.key))
    assert jul.severity == "not_available"
    assert not db_session.query(DataException).filter(
        DataException.status == "open", DataException.source_pk.like("%主力%2026-07%")).all()


def test_main_covered_and_balanced_ok(db_session):
    """主力号: 流水覆盖到快照日且滚得平 → ok。"""
    _ab(db_session, "主力号", 2026, 6, op=8000, cl=8000, as_of=date(2026, 6, 30))
    _ab(db_session, "主力号", 2026, 7, op=8000, cl=10000, as_of=date(2026, 7, 10))
    _fl(db_session, "主力号", datetime(2026, 7, 10, 9, 0), 2000)
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=False)
    jul = next(x for x in res.diffs if "主力" in str(x.key) and "2026-07" in str(x.key))
    assert jul.severity == "ok"


def test_main_dead_anchor_skips(db_session):
    """主力号: 上月锚点已作废(as_of=None, 误读企业账户的脏数) → not_available 跳过。"""
    _ab(db_session, "主力号", 2026, 6, op=418005.65, cl=5475.76, as_of=None)   # 作废的脏锚点
    _ab(db_session, "主力号", 2026, 7, op=5475.76, cl=10250.49, as_of=date(2026, 7, 10))
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=True)
    db_session.commit()
    jul = next(x for x in res.diffs if "主力" in str(x.key) and "2026-07" in str(x.key))
    assert jul.severity == "not_available"

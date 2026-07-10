# -*- coding: utf-8 -*-
"""推广户账面核对改流水滚动 (2026-07-10 治本).

老逻辑: 期初+收入-支出=期末, 但推广行的收支从来没人维护(恒0) → 每月必炸僵尸异常(实测41698)。
新逻辑: 上次快照(as_of) + 窗口充值 - 窗口扣款 ≈ 本次快照, 容差=max(100, 2×窗口日均扣款)。
"""
from datetime import date
from decimal import Decimal as D

from app.models.exception import DataException
from app.models.finance import AccountBalance
from app.models.marketing import PromotionFlow
from app.services import reconciliation_service as rs


def _ab(db, y, m, op, cl, as_of=None):
    db.add(AccountBalance(account_name="淘宝推广账户", period_year=y, period_month=m,
                          opening_balance=D(str(op)), closing_balance=D(str(cl)),
                          income=D("0"), expense=D("0"), as_of_date=as_of))


def _pf(db, d, ftype, amt):
    db.add(PromotionFlow(transaction_date=d, flow_type=ftype, amount=D(str(amt))))


def _promo_diffs(res):
    return [x for x in res.diffs if "推广" in str(x.key)]


def test_promo_flow_rolls_balanced_within_tolerance(db_session):
    """实测形态: 5234.79(06-29) - 窗口扣款1845.60 = 3389.19 vs 快照3047.73, 差341≈一天扣款 → 容差内 ok。
    (老逻辑这行收支=0 必报"账面不自洽"; 新逻辑不再报。)"""
    _ab(db_session, 2026, 6, op=3553.44, cl=5234.79, as_of=date(2026, 6, 29))
    _ab(db_session, 2026, 7, op=5234.79, cl=3047.73, as_of=date(2026, 7, 9))
    for dd, amt in [(date(2026, 6, 30), 157.36), (date(2026, 7, 1), 149.98), (date(2026, 7, 2), 76.29),
                    (date(2026, 7, 3), 80.27), (date(2026, 7, 4), 175.23), (date(2026, 7, 5), 183.85),
                    (date(2026, 7, 6), 311.73), (date(2026, 7, 7), 338.24), (date(2026, 7, 8), 372.65)]:
        _pf(db_session, dd, "支出", amt)
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=True)
    db_session.commit()
    jul = next(x for x in _promo_diffs(res) if "2026-07" in str(x.key))
    assert jul.severity == "ok"                       # 流水滚动对平(容差内)
    opens = db_session.query(DataException).filter(
        DataException.status == "open",
        DataException.source_pk.like("%推广%2026-07%")).all()
    assert not opens                                   # 不再生成僵尸异常


def test_promo_flow_flags_when_recharge_missing(db_session):
    """快照涨了5000但窗口内没有任何充值流水 → 滚不平, 该报(真实缺数据)。"""
    _ab(db_session, 2026, 6, op=1000, cl=1000, as_of=date(2026, 6, 29))
    _ab(db_session, 2026, 7, op=1000, cl=6000, as_of=date(2026, 7, 9))
    _pf(db_session, date(2026, 7, 2), "支出", 100)
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=False)
    jul = next(x for x in _promo_diffs(res) if "2026-07" in str(x.key))
    assert jul.severity not in ("ok", "not_available")   # 差5100, 远超容差 → 报


def test_promo_no_asof_skips_not_available(db_session):
    """上月快照缺 as_of(旧手填行)→ 定不了窗口 → not_available 跳过, 不硬判不炸。"""
    _ab(db_session, 2026, 5, op=3553.44, cl=3553.44, as_of=None)
    _ab(db_session, 2026, 6, op=3553.44, cl=5234.79, as_of=date(2026, 6, 29))
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=True)
    db_session.commit()
    jun = next(x for x in _promo_diffs(res) if "2026-06" in str(x.key))
    assert jun.severity == "not_available"
    opens = db_session.query(DataException).filter(
        DataException.status == "open",
        DataException.source_pk.like("%推广%2026-06%")).all()
    assert not opens

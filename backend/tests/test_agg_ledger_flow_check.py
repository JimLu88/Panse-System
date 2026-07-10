# -*- coding: utf-8 -*-
"""聚合户账面核对改微信明细滚动 (2026-07-10 治本, 同推广户思路).

聚合结算账户(千牛资金页)只装微信钱, 明细= order_settlements 的 wechat/agent 源(billDetail每日拉);
企业号支付宝分账(source=alipay)的钱在支付宝企业账户, 不许掺进聚合户滚动。
实测形态: 57855.45(06-29) + 微信净额2634.00 = 60489.45(07-09), 分毫不差。
"""
from datetime import date, datetime
from decimal import Decimal as D

from app.models.exception import DataException
from app.models.finance import AccountBalance
from app.models.settlement import OrderSettlement
from app.services import reconciliation_service as rs


def _ab(db, y, m, op, cl, as_of=None):
    db.add(AccountBalance(account_name="淘宝聚合账户", period_year=y, period_month=m,
                          opening_balance=D(str(op)), closing_balance=D(str(cl)),
                          income=D("0"), expense=D("0"), as_of_date=as_of))


def _st(db, dt, inc, exp, source="agent", pay_no=None):
    db.add(OrderSettlement(source=source, pay_no=pay_no or f"P{dt:%m%d%H%M%S}{inc}{exp}",
                           settle_time=dt, entry_type="交易收款",
                           income=D(str(inc)), expense=D(str(exp))))


def _agg_diffs(res):
    return [x for x in res.diffs if "聚合" in str(x.key)]


def test_agg_rolls_exact_with_wechat_detail(db_session):
    """实测形态: 06-29 57855.45 + (200-1.20+2824.50-16.95-372.35=2634.00) = 60489.45 → ok 不报。"""
    _ab(db_session, 2026, 6, op=57855.45, cl=57855.45, as_of=date(2026, 6, 29))
    _ab(db_session, 2026, 7, op=57855.45, cl=60489.45, as_of=date(2026, 7, 9))
    _st(db_session, datetime(2026, 7, 3, 21, 52), 200.00, 1.20)
    _st(db_session, datetime(2026, 7, 6, 20, 23), 2824.50, 16.95)
    _st(db_session, datetime(2026, 7, 6, 20, 25), 0, 372.35)
    # 干扰: 窗口内企业号支付宝分账(source=alipay) 大额 — 不许掺进聚合户
    _st(db_session, datetime(2026, 7, 5, 12, 0), 36000.00, 0, source="alipay")
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=True)
    db_session.commit()
    jul = next(x for x in _agg_diffs(res) if "2026-07" in str(x.key))
    assert jul.severity == "ok"
    opens = db_session.query(DataException).filter(
        DataException.status == "open",
        DataException.source_pk.like("%聚合%2026-07%")).all()
    assert not opens


def test_agg_flags_when_detail_missing(db_session):
    """快照涨5000但窗口内没有任何微信明细 → 滚不平, 该报(真实缺数据)。"""
    _ab(db_session, 2026, 6, op=1000, cl=1000, as_of=date(2026, 6, 29))
    _ab(db_session, 2026, 7, op=1000, cl=6000, as_of=date(2026, 7, 9))
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=False)
    jul = next(x for x in _agg_diffs(res) if "2026-07" in str(x.key))
    assert jul.severity not in ("ok", "not_available")


def test_agg_no_asof_skips(db_session):
    """缺 as_of 定不了窗口 → not_available 跳过不硬判。"""
    _ab(db_session, 2026, 6, op=1000, cl=1000, as_of=None)
    _ab(db_session, 2026, 7, op=1000, cl=6000, as_of=date(2026, 7, 9))
    db_session.commit()
    res = rs.run_ledger_check(db_session, record_exceptions=True)
    db_session.commit()
    jul = next(x for x in _agg_diffs(res) if "2026-07" in str(x.key))
    assert jul.severity == "not_available"

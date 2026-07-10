# -*- coding: utf-8 -*-
"""物流费对账·运费月份标签 (2026-07-10): 攒几个月一起付的运费按备注「X月」记到账单月;
部分结款(多承运商各付各的/余款走其他渠道)按待结口径提示不报警。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from datetime import date, datetime
from decimal import Decimal as D

from app.models.exception import DataException
from app.models.finance import AlipayFlow, LogisticsBill
from app.services import reconciliation_service as rec


def _bill(db, d, amt):
    db.add(LogisticsBill(bill_date=d, freight_amount=D(str(amt)), row_type="line", carrier="综合"))


def _fl(db, tno, dt, amt, remark):
    db.add(AlipayFlow(account="主力号", transaction_no=tno, transaction_type="转账",
                      amount=D(str(amt)), balance=D("0"), counterparty="玉山县佳吉联运有限公司",
                      remark=remark, reconciliation_type="logistics", transaction_time=dt))


def test_month_tag_buckets_to_bill_month(db_session):
    """实测形态: 5月账单9259 = 挚乐5893(06-27付,标'5月') + 德邦3366(06-30付,标'5月') → 5月平, 差0。"""
    _bill(db_session, date(2026, 5, 15), 9259)
    _fl(db_session, "LG1", datetime(2026, 6, 27, 10, 0), -5893, "挚乐5月运费")
    _fl(db_session, "LG2", datetime(2026, 6, 30, 10, 0), -3366, "德邦运费5月 德邦运费5月")
    db_session.commit()
    res = rec.run_logistics_fee(db_session, record_exceptions=False)
    may = next(x for x in res.diffs if x.key == "2026-05")
    assert may.severity == "ok" and abs(D(str(may.diff))) < D("0.01")


def test_partial_payment_informational(db_session):
    """2月账单7988 只付了挚乐6958.5 → 部分结款提示(ok), 不产生异常。"""
    _bill(db_session, date(2026, 2, 15), 7988)
    _fl(db_session, "LG3", datetime(2026, 6, 27, 10, 0), -6958.5, "挚乐2月运费")
    db_session.commit()
    res = rec.run_logistics_fee(db_session, record_exceptions=True)
    db_session.commit()
    feb = next(x for x in res.diffs if x.key == "2026-02")
    assert feb.severity == "ok" and "部分结款" in str(feb.message)
    assert not db_session.query(DataException).filter(
        DataException.status == "open",
        DataException.source_pk.like("%logistics%2026-02%")).all()


def test_untagged_flow_uses_payment_month(db_session):
    """无月份标签的运费落交易月: 6月账单100, 6月付100(无标签) → 6月平。"""
    _bill(db_session, date(2026, 6, 15), 100)
    _fl(db_session, "LG4", datetime(2026, 6, 20, 10, 0), -100, "散单运费-顺丰速运")
    db_session.commit()
    res = rec.run_logistics_fee(db_session, record_exceptions=False)
    jun = next(x for x in res.diffs if x.key == "2026-06")
    assert jun.severity == "ok" and abs(D(str(jun.diff))) < D("0.01")

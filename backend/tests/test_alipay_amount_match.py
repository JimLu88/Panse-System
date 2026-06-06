"""对账细化4规则: alipay_amount_match_service (金额唯一/金额+日期/多对一·一对多/账户语义)。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.models.order import Order
from app.services import alipay_amount_match_service as m


def _order(db, no, amt, d="2026-06-01"):
    db.add(Order(platform="淘宝", order_no=no, paid_amount=Decimal(str(amt)),
                 order_date=date.fromisoformat(d), status="signed"))
    db.commit()


def _flow(db, no, amt, account="主力号", t="2026-06-01 10:00:00"):
    db.add(AlipayFlow(account=account, transaction_no=no, amount=Decimal(str(amt)),
                      transaction_time=datetime.fromisoformat(t), reconciliation_status="open"))
    db.commit()


def test_r2_amount_unique_lock(db_session):
    _order(db_session, "O1", 888)
    _flow(db_session, "F1", 888)
    _order(db_session, "O2", 100); _flow(db_session, "F2", 200)   # 噪声(各自唯一但不相等)
    res = m.match(db_session)
    assert db_session.query(Order).filter_by(order_no="O1").one().alipay_flow_no == "F1"
    assert res.by_rule.get("amount_unique") == 1


def test_r1_amount_date_unique(db_session):
    _order(db_session, "O1", 500, "2026-06-01")
    _flow(db_session, "F1", 500, t="2026-06-02 09:00:00")   # 窗口内
    _flow(db_session, "F2", 500, t="2026-06-20 09:00:00")   # 窗口外
    res = m.match(db_session)
    assert db_session.query(Order).filter_by(order_no="O1").one().alipay_flow_no == "F1"
    assert res.by_rule.get("amount_date") == 1


def test_r3_split_order_to_two_flows(db_session):
    _order(db_session, "O1", 300, "2026-06-01")
    _flow(db_session, "F1", 100, t="2026-06-01 09:00:00")
    _flow(db_session, "F2", 200, t="2026-06-01 10:00:00")
    res = m.match(db_session)
    assert db_session.query(Order).filter_by(order_no="O1").one().alipay_flow_no in ("F1", "F2")
    assert res.by_rule.get("split_order_to_flows") == 1
    assert all(f.reconciliation_status == "matched" for f in db_session.query(AlipayFlow).all())


def test_r3_merge_two_orders_to_one_flow(db_session):
    _order(db_session, "O1", 100, "2026-06-01")
    _order(db_session, "O2", 200, "2026-06-01")
    _flow(db_session, "F1", 300, t="2026-06-01 10:00:00")
    res = m.match(db_session)
    assert db_session.query(Order).filter_by(order_no="O1").one().alipay_flow_no == "F1"
    assert db_session.query(Order).filter_by(order_no="O2").one().alipay_flow_no == "F1"
    assert res.by_rule.get("merge_flows_to_order") == 2


def test_r4_excludes_deprecated_account_and_expense(db_session):
    _order(db_session, "O1", 888)
    _flow(db_session, "F1", 888, account="爱群号")     # 弃用账户 -> 排除
    _flow(db_session, "F2", -50, account="主力号")      # 支出 -> 排除
    res = m.match(db_session)
    assert db_session.query(Order).filter_by(order_no="O1").one().alipay_flow_no is None
    assert res.matched == 0


def test_only_missing_does_not_overwrite(db_session):
    _order(db_session, "O1", 888)
    db_session.query(Order).filter_by(order_no="O1").one().alipay_flow_no = "EXISTING"
    db_session.commit()
    _flow(db_session, "F1", 888)
    m.match(db_session)
    assert db_session.query(Order).filter_by(order_no="O1").one().alipay_flow_no == "EXISTING"

from decimal import Decimal

from app.models.finance import AlipayFlow
from app.services import smart_matching_service


def _flow(db, tx, amount, counterparty=None, remark=None, related_order_no=None, account="A"):
    f = AlipayFlow(
        account=account, transaction_no=tx, amount=Decimal(str(amount)),
        counterparty=counterparty, remark=remark, related_order_no=related_order_no,
    )
    db.add(f)
    db.flush()
    return f


def test_factory_payment_tagged(db_session):
    _flow(db_session, "T1", -10000, counterparty="玉山县博冠家具有限公司")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"factory_payment": 1}
    f = db_session.query(AlipayFlow).filter_by(transaction_no="T1").one()
    assert f.reconciliation_type == "factory_payment"


def test_promotion_tagged(db_session):
    _flow(db_session, "T1", -500, counterparty="淘宝商业", remark="现金消耗扣款")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"promotion": 1}


def test_logistics_tagged(db_session):
    _flow(db_session, "T1", -300, counterparty="万师傅平台")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"logistics": 1}


def test_salary_tagged(db_session):
    _flow(db_session, "T1", -5000, counterparty="李爱群", remark="工资")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"salary": 1}


def test_customer_payment_tagged(db_session):
    _flow(db_session, "T1", 100, related_order_no="淘宝5112861625016010242")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"customer_payment": 1}


def test_already_tagged_untouched(db_session):
    f = _flow(db_session, "T1", -10000, counterparty="博冠家具")
    f.reconciliation_type = "manual_override"
    db_session.flush()
    r = smart_matching_service.run(db_session)
    assert r.tagged == {}
    assert r.untouched == 0  # untouched 仅指未分类那批未匹配上的
    db_session.refresh(f)
    assert f.reconciliation_type == "manual_override"


def test_no_match_stays_untagged(db_session):
    _flow(db_session, "T1", -100, counterparty="未知商户", remark="未知")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {}
    assert r.untouched == 1


def test_mixed_batch(db_session):
    _flow(db_session, "T1", -10000, counterparty="博冠家具")
    _flow(db_session, "T2", -500, counterparty="淘宝商业")
    _flow(db_session, "T3", 200, related_order_no="淘宝123")
    _flow(db_session, "T4", -50, counterparty="不知道是啥")
    r = smart_matching_service.run(db_session)
    assert r.total_scanned == 4
    assert r.tagged == {"factory_payment": 1, "promotion": 1, "customer_payment": 1}
    assert r.untouched == 1

"""支付宝流水 → 订单 反向匹配回填 (自己找规律)."""
from decimal import Decimal

from app.models.finance import AlipayFlow
from app.models.order import Order
from app.services import alipay_backfill_service


def _order(db, order_no):
    o = Order(platform="淘宝", order_no=order_no, qty=1, status="signed")
    db.add(o)
    db.flush()
    return o


def _flow(db, tx, amount, related_order_no=None, remark=None,
          counterparty_account=None, account="企业号"):
    f = AlipayFlow(
        account=account, transaction_no=tx, amount=Decimal(str(amount)),
        related_order_no=related_order_no, remark=remark,
        counterparty_account=counterparty_account,
    )
    db.add(f)
    db.flush()
    return f


def test_t200p_prefix_stripped_and_matched(db_session):
    """'T200P2701846635029001 070' → 去前缀去空格 → 命中订单 2701846635029001070."""
    _order(db_session, "2701846635029001070")
    _flow(db_session, "FLOW1", 127.00, related_order_no="T200P2701846635029001 070")

    res = alipay_backfill_service.backfill(db_session)
    assert res.filled_flow_no == 1
    o = db_session.query(Order).filter_by(order_no="2701846635029001070").one()
    assert o.alipay_flow_no == "FLOW1"


def test_order_no_extracted_from_remark(db_session):
    """订单号藏在备注里: '基础软件服务费(2701791878046047055)扣款'."""
    _order(db_session, "2701791878046047055")
    _flow(db_session, "FLOW2", -16.23, related_order_no=None,
          remark="基础软件服务费(2701791878046047055)扣款")

    res = alipay_backfill_service.backfill(db_session)
    assert res.filled_flow_no == 1
    o = db_session.query(Order).filter_by(order_no="2701791878046047055").one()
    assert o.alipay_flow_no == "FLOW2"


def test_exact_match(db_session):
    """关联订单号直接就是订单号 (9c 爱群号那种)."""
    _order(db_session, "3043704615516645288")
    _flow(db_session, "FLOW3", 500, related_order_no="3043704615516645288")

    res = alipay_backfill_service.backfill(db_session)
    assert res.filled_flow_no == 1
    assert res.by_rule.get("exact") == 1


def test_no_false_match_for_unrelated_flow(db_session):
    """无订单号关联的流水 (理财/手续费) 不应误匹配."""
    _order(db_session, "5112861625016010242")
    # 理财流水, 数字串和任何订单号都对不上
    _flow(db_session, "FLOW4", -1000, related_order_no="202604232000400111006 80090321541")

    res = alipay_backfill_service.backfill(db_session)
    assert res.filled_flow_no == 0
    o = db_session.query(Order).filter_by(order_no="5112861625016010242").one()
    assert o.alipay_flow_no is None


def test_only_missing_does_not_overwrite(db_session):
    o = _order(db_session, "2701846635029001070")
    o.alipay_flow_no = "EXISTING"
    db_session.flush()
    _flow(db_session, "FLOW5", 100, related_order_no="T200P2701846635029001 070")

    res = alipay_backfill_service.backfill(db_session, only_missing=True)
    assert res.filled_flow_no == 0
    db_session.refresh(o)
    assert o.alipay_flow_no == "EXISTING"


def test_analyze_is_readonly(db_session):
    _order(db_session, "2701846635029001070")
    _flow(db_session, "FLOW6", 127, related_order_no="T200P2701846635029001 070")

    res = alipay_backfill_service.analyze(db_session)
    assert res.matched_orders == 1
    assert res.filled_flow_no == 0  # analyze 不写库
    o = db_session.query(Order).filter_by(order_no="2701846635029001070").one()
    assert o.alipay_flow_no is None
    assert len(res.samples) == 1


def test_ambiguous_flow_skipped(db_session):
    """一条流水掏出两个不同订单号 → 歧义, 跳过不回填."""
    _order(db_session, "2701846635029001070")
    _order(db_session, "3043704615516645288")
    _flow(db_session, "FLOW7", 100,
          related_order_no="2701846635029001070",
          remark="合并支付 3043704615516645288")

    res = alipay_backfill_service.backfill(db_session)
    assert res.ambiguous == 1
    assert res.filled_flow_no == 0


def test_income_flow_preferred_over_expense(db_session):
    """同订单多条流水命中时, 收入(客户回款)优先于支出."""
    _order(db_session, "2701846635029001070")
    # 支出流水 (分账手续费)
    _flow(db_session, "FEE", -0.76, related_order_no="T200P2701846635029001 070")
    # 收入流水 (客户付款)
    _flow(db_session, "PAY", 127.00, related_order_no="T200P2701846635029001 070")

    res = alipay_backfill_service.backfill(db_session)
    assert res.filled_flow_no == 1
    o = db_session.query(Order).filter_by(order_no="2701846635029001070").one()
    assert o.alipay_flow_no == "PAY"  # 认收入那条

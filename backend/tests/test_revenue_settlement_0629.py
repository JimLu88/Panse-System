# -*- coding: utf-8 -*-
"""营收对账纳入 order_settlements 交易收款 (聚合/微信收款, 用户 2026-06-29)。

聚合结算账户收款走 billDetail → order_settlements 的『交易收款』, 不进 alipay_flows。
营收对账过去只看 alipay_flows → 聚合付款订单假报"未配到流水"。本测试验证: 该单被认领、不再误报。
"""
from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.models.settlement import OrderSettlement
from app.services import reconciliation_service as rs


def test_revenue_alipay_counts_aggregate_settlement(db_session):
    """聚合付款订单(收款仅在 order_settlements 交易收款)→ 营收对账认它为该单收入, 不算"未配到流水"。"""
    db_session.add(Order(platform="淘宝", order_no="5100000000000000001", status="signed", is_refill=False,
                         order_date=date(2026, 3, 1), paid_amount=Decimal("3183.78"),
                         buyer_payable_amount=Decimal("3183.78")))
    db_session.add(OrderSettlement(source="agent", pay_no="PAYAGG1", order_no="5100000000000000001",
                                   entry_type="交易收款", income=Decimal("3183.78"), expense=Decimal("0")))
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    # 2026-03 月度兜底的"未配到流水的订单实付"应为 0(AGG1 被聚合结算认领); 旧口径会是 3183.78
    march = [d for d in res.diffs if d.key == "2026-03 兜底"]
    assert not march or (march[0].expected or 0) == 0


def test_settlement_deduction_not_counted_as_income(db_session):
    """『扣款』(软件服务费, income=0) 不算收款 → 不影响该单收入认领。"""
    db_session.add(Order(platform="淘宝", order_no="5100000000000000002", status="signed", is_refill=False,
                         order_date=date(2026, 3, 1), paid_amount=Decimal("100"),
                         buyer_payable_amount=Decimal("100")))
    db_session.add(OrderSettlement(source="agent", pay_no="PAYAGG2", order_no="5100000000000000002",
                                   entry_type="交易收款", income=Decimal("100"), expense=Decimal("0")))
    db_session.add(OrderSettlement(source="agent", pay_no="FEEAGG2", order_no="5100000000000000002",
                                   entry_type="扣款", income=Decimal("0"), expense=Decimal("0.13")))
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    march = [d for d in res.diffs if d.key == "2026-03 兜底"]
    assert not march or (march[0].expected or 0) == 0


def test_unsigned_order_not_flagged_unmatched(db_session):
    """未签收订单(status=paid, 担保未放款)即便>45天、无流水 → 不算"未配到流水"。"""
    from datetime import timedelta
    old = date.today() - timedelta(days=60)
    db_session.add(Order(platform="淘宝", order_no="5100000000000000003", status="paid", is_refill=False,
                         order_date=old, paid_amount=Decimal("5000"), buyer_payable_amount=Decimal("5000")))
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    assert all((d.expected or 0) == 0 for d in res.diffs if d.key and "兜底" in d.key)


def test_signed_order_no_flow_still_flagged(db_session):
    """已签收订单(放款应已触发)>45天、无流水 → 仍报"未配到流水"(真缺口)。"""
    from datetime import timedelta
    old = date.today() - timedelta(days=60)
    db_session.add(Order(platform="淘宝", order_no="5100000000000000004", status="signed", is_refill=False,
                         order_date=old, paid_amount=Decimal("5000"), buyer_payable_amount=Decimal("5000")))
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    total = sum(float(d.expected or 0) for d in res.diffs if d.key and "兜底" in d.key)
    assert total == 5000.0


def test_settlement_noise_orphan_excluded(db_session):
    """伪订单号(T200P消费券/HJCAEB商户号等)的支付宝收入配不到订单 → 全排, 不进月度兜底收入侧 (用户 2026-06-29)。"""
    from datetime import datetime, timezone
    from app.models.finance import AlipayFlow
    # 一个已配平的真单(走聚合结算 order_settlements)
    db_session.add(Order(platform="淘宝", order_no="5100000000000000009", status="signed", is_refill=False,
                         order_date=date(2026, 5, 1), paid_amount=Decimal("100"), buyer_payable_amount=Decimal("100")))
    db_session.add(OrderSettlement(source="agent", pay_no="PAY9", order_no="5100000000000000009",
                                   entry_type="交易收款", income=Decimal("100"), expense=Decimal("0")))
    # 一笔 T200P 消费券噪音(配不到任何订单)
    db_session.add(AlipayFlow(account="企业号", transaction_no="TNNOISE1", related_order_no="T200P4931308621633047840",
                              amount=Decimal("5000"), transaction_time=datetime(2026, 5, 15, tzinfo=timezone.utc)))
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    # 收入侧(actual)应全 0:T200P 被排除, 月度兜底无差
    assert all((d.actual or 0) == 0 for d in res.diffs if d.key and "兜底" in d.key)


def test_real_digit_orphan_still_flagged(db_session):
    """纯数字订单号但配不到系统订单(疑漏导真单)→ 仍报收入侧(精确: 只排噪音不掩真缺单)。"""
    from datetime import datetime, timezone
    from app.models.finance import AlipayFlow
    db_session.add(AlipayFlow(account="企业号", transaction_no="TNREAL1", related_order_no="5119999999999999999",
                              amount=Decimal("3000"), transaction_time=datetime(2026, 5, 16, tzinfo=timezone.utc)))
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    total_act = sum(float(d.actual or 0) for d in res.diffs if d.key and "兜底" in d.key)
    assert total_act == 3000.0


# --- 同交易号去重: 定金+尾款共号相加 vs 付款+分账不双算 (用户 2026-06-29, 修 5117408) ---

def _flow(db, *, txn, order_no, amt, ttype, day=10):
    from datetime import datetime, timezone
    from app.models.finance import AlipayFlow
    db.add(AlipayFlow(account="企业号", transaction_no=txn, related_order_no=order_no,
                      amount=Decimal(str(amt)), transaction_type=ttype,
                      transaction_time=datetime(2026, 5, day, tzinfo=timezone.utc)))


def _order(db, order_no, paid):
    db.add(Order(platform="淘宝", order_no=order_no, status="signed", is_refill=False,
                 order_date=date(2026, 5, 1), paid_amount=Decimal(str(paid)),
                 buyer_payable_amount=Decimal(str(paid))))


def _order_diff(res, order_no):
    """该订单的逐单差(平账则不在 diffs 里 → None)。"""
    hits = [d for d in res.diffs if d.key == order_no]
    return hits[0] if hits else None


def test_deposit_and_final_same_txn_summed(db_session):
    """定金+尾款共用同一交易号(都是交易付款)→ 相加 6333.66 = 实付, 平账(旧『取最大』会少算 2706 误报短收)。"""
    _order(db_session, "5117408713503179541", 6333.66)
    _flow(db_session, txn="TXNSHARED", order_no="5117408713503179541", amt=3627.49, ttype="交易付款", day=8)
    _flow(db_session, txn="TXNSHARED", order_no="5117408713503179541", amt=2706.17, ttype="交易付款", day=20)
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    assert _order_diff(res, "5117408713503179541") is None   # 6333.66 对平, 无短收异常


def test_payment_plus_split_not_doubled(db_session):
    """交易付款 + 分账(同号同额=同一笔钱的镜像)→ 只算一次 1000, 不虚高 2 倍(守住旧去重不变)。"""
    _order(db_session, "5100000000000000021", 1000)
    _flow(db_session, txn="TXNMIRROR", order_no="5100000000000000021", amt=1000, ttype="交易付款")
    _flow(db_session, txn="TXNMIRROR", order_no="5100000000000000021", amt=1000, ttype="分账")
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    d = _order_diff(res, "5100000000000000021")
    assert d is None or float(d.actual or 0) == 1000.0   # 收入认 1000 非 2000


def test_only_split_falls_back_to_max(db_session):
    """某交易号下只有分账没有交易付款 → 回退取最大(不丢收入)。"""
    _order(db_session, "5100000000000000022", 500)
    _flow(db_session, txn="TXNONLYSPLIT", order_no="5100000000000000022", amt=500, ttype="交易分账")
    db_session.flush()
    res = rs.run_revenue_alipay(db_session, record_exceptions=False)
    d = _order_diff(res, "5100000000000000022")
    assert d is None or float(d.actual or 0) == 500.0

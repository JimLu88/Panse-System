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


def test_logistics_yimidida_tagged(db_session):
    """壹米滴答运费(支出) → logistics (用户 2026-06-24 加关键字)。"""
    _flow(db_session, "TY", -11345, remark="江西壹米滴答12月运费")
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

# ── 对账优化①: 关联订单号归一化 + 数据驱动工厂名 ──────────────────────────────

def test_related_order_no_links_to_real_order(db_session):
    """收入流水关联订单号(带前缀+空格)归一化后命中真实订单 → customer_payment."""
    from app.models.order import Order
    db_session.add(Order(platform="淘宝", order_no="2701846635029001070", status="signed"))
    db_session.flush()
    _flow(db_session, "T1", 127.00, related_order_no="T200P2701846635029001 070")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"customer_payment": 1}


def test_related_order_no_links_to_factory_order(db_session):
    """支出流水关联订单号命中工厂下单 platform_order_no → factory_payment."""
    from app.models.order import FactoryOrder
    db_session.add(FactoryOrder(factory_order_no="FO1", platform_order_no="2701846635029001070", factory_name="博冠"))
    db_session.flush()
    _flow(db_session, "T1", -3000, related_order_no="T200P2701846635029001 070")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"factory_payment": 1}


def test_dynamic_factory_name_match(db_session):
    """对手方命中库内真实工厂名(非硬编码关键字) → factory_payment."""
    from app.models.order import FactoryOrder
    db_session.add(FactoryOrder(factory_order_no="FO2", platform_order_no="X1", factory_name="玉山县大美木制品厂"))
    db_session.flush()
    _flow(db_session, "T1", -8000, counterparty="玉山县大美木制品厂")
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"factory_payment": 1}


def test_refund_precedence_over_factory_match(db_session):
    """退款优先(治本): 退给买家的钱(amt<0)挂客户订单, 该单又有工厂单 →
    不能像段1那样误判 factory_payment, 必须判 refund (实测 23 笔虚高工厂货款的根因)。"""
    from app.models.order import FactoryOrder
    db_session.add(FactoryOrder(factory_order_no="FO9", platform_order_no="3165874608994317861",
                                factory_name="博冠"))
    db_session.flush()
    f = _flow(db_session, "TR1", -50, remark="售后退款-2026010522001186861400199190-T200P3165874608994317861",
              related_order_no="3165874608994317861_258419316786316178")
    f.transaction_type = "交易退款"
    db_session.flush()
    r = smart_matching_service.run(db_session)
    assert r.tagged == {"refund": 1}           # 不是 factory_payment
    db_session.refresh(f)
    assert f.reconciliation_type == "refund"


def test_reclassify_refund_mislabels(db_session):
    """存量纠正: 被误标 factory_payment 的交易退款(amt<0) → 改判 refund; 真货款 + 已 refund 的不动。"""
    a = _flow(db_session, "MR1", -50, remark="售后退款 T200P x")
    a.transaction_type = "交易退款"; a.reconciliation_type = "factory_payment"
    b = _flow(db_session, "MR2", -16536, counterparty="博冠家具", remark="付货款")
    b.reconciliation_type = "factory_payment"   # 真工厂货款, 无退款字样 → 不动
    c = _flow(db_session, "MR3", -30, remark="售后退款")
    c.transaction_type = "交易退款"; c.reconciliation_type = "refund"  # 已正确 → 不重复
    db_session.flush()
    detail = smart_matching_service.reclassify_refund_mislabels(db_session)
    db_session.refresh(a); db_session.refresh(b); db_session.refresh(c)
    assert a.reconciliation_type == "refund"
    assert b.reconciliation_type == "factory_payment"
    assert c.reconciliation_type == "refund"
    assert detail.get("factory_payment", {}).get("count") == 1


def test_order_key_normalization():
    from app.services.smart_matching_service import _order_key
    assert _order_key("T200P2701846635029001 070") == "2701846635029001070"
    assert _order_key("淘宝123") is None       # 短串不当订单号
    assert _order_key(None) is None
    assert _order_key("  ") is None

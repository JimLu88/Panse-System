"""现金流「可用资金」口径修正 (用户 2026-07-01 审计后):

F1(C3) 未开账单预测成本改用统一 physical_cost(同月度经营/逐单核对), 缺成本单不再被丢弃;
F2     新增"平台活动抽成(在途×2%)", 只对下单月落在活动窗口(5月起)的在途单计;
F3(C9) 已开账单按是否挂真实订单分流(打样 vs 结算), 预测成本去重加 source_order_id, 根治双扣。
"""
from datetime import date
from decimal import Decimal

from app.models.order import FactoryOrder, Order
from app.services import cash_flow_service as cfs
from app.services import order_financials as ofin


def _order(db, no, *, status="paid", paid="1000", **kw):
    o = Order(platform="淘宝", order_no=no, status=status, paid_amount=Decimal(str(paid)), **kw)
    db.add(o)
    db.flush()
    return o


def _subs(db):
    s = cfs.compute_summary(db)
    return {x["key"]: x["amount"] for x in s["subtractions"]}


def test_c9_double_count_fixed_via_source_order_id(db_session):
    """已开账单·无平台单号·挂了 source_order_id → 只进「已开账单未付」,
    既不误归打样费、也被预测成本去重(不再双扣)。"""
    db = db_session
    o = _order(db, "D1", status="paid", paid="2000", theoretical_cost=Decimal("700"))
    db.add(FactoryOrder(factory_order_no="FD1", payment_status="unpaid",
                        platform_order_no=None, source_order_id=o.id,
                        factory_bill_amount=Decimal("600")))
    db.flush()
    sub = _subs(db)
    assert sub["factory_billed"] == Decimal("600")   # 归已开账单未付
    assert sub["factory_sample"] == Decimal("0")      # 不再误归打样
    assert sub["factory_estimate"] == Decimal("0")    # 已开账单 → 预测成本去重跳过(根治双扣)


def test_genuine_sample_stays_in_sample(db_session):
    """无平台单号且无订单链接 = 真打样 → 进打样费。"""
    db = db_session
    db.add(FactoryOrder(factory_order_no="FS1", payment_status="unpaid",
                        platform_order_no=None, source_order_id=None,
                        factory_bill_amount=Decimal("150")))
    db.flush()
    sub = _subs(db)
    assert sub["factory_sample"] == Decimal("150")
    assert sub["factory_billed"] == Decimal("0")


def test_c3_custom_missing_cost_now_deducted(db_session):
    """C3: 定制缺成本的活跃单, 预测成本走统一 physical_cost(定制兜底85), 不再被静默丢弃。"""
    db = db_session
    o = _order(db, "C1", status="paid", paid="5000", is_custom=True)  # 无 theoretical/actual
    expected = ofin.physical_cost(o)
    sub = _subs(db)
    assert expected > 0                            # 定制兜底给出 >0 成本(实付×85%)
    assert sub["factory_estimate"] == expected      # 计入减项(不再营收进/成本不进)


def test_f2_activity_2pct_only_in_window(db_session):
    """F2: 2% 活动抽成只对下单月在活动窗口(默认5月起)的在途单计, 1-4月不计。"""
    db = db_session
    _order(db, "MAY", status="paid", paid="10000", order_date=date(2026, 5, 10))
    _order(db, "APR", status="paid", paid="10000", order_date=date(2026, 4, 10))
    sub = _subs(db)
    assert sub["platform_activity"] == Decimal("200.00")   # 仅5月单 10000×2%; 4月单不计


def test_f2_activity_zero_when_no_window_orders(db_session):
    """无下单日期 / 全在窗口外 → 活动抽成为0(不误扣)。"""
    db = db_session
    _order(db, "NODATE", status="paid", paid="9999")        # 无 order_date
    _order(db, "APR2", status="paid", paid="8888", order_date=date(2026, 4, 1))
    sub = _subs(db)
    assert sub["platform_activity"] == Decimal("0.00")

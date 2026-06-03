from decimal import Decimal

from app.models.finance import AccountBalance
from app.models.order import FactoryOrder, Order
from app.services import cash_flow_service


def _add_balance(db, name, year, month, closing):
    db.add(AccountBalance(
        account_name=name, period_year=year, period_month=month,
        opening_balance=Decimal("0"), income=Decimal("0"),
        expense=Decimal("0"), closing_balance=Decimal(str(closing)),
    ))


def _add_order(db, order_no, status, paid, fee=None, is_refill=False):
    db.add(Order(
        platform="淘宝", order_no=order_no, status=status,
        paid_amount=Decimal(str(paid)),
        platform_fee=None if fee is None else Decimal(str(fee)),
        is_refill=is_refill,
    ))


def test_cash_flow_summary_formula(db_session):
    db = db_session

    # 账户余额 — 每个账户多期，应取最新期
    _add_balance(db, "支付宝-企业账号", 2026, 4, 100000)
    _add_balance(db, "支付宝-企业账号", 2026, 5, 207095.16)   # 最新
    _add_balance(db, "支付宝-个体户私人", 2026, 5, 4956.32)
    _add_balance(db, "淘宝聚合账户", 2026, 5, 44481.56)
    _add_balance(db, "淘宝推广账户", 2026, 5, 3553.44)
    _add_balance(db, "银行卡-个体户私人", 2026, 5, 30000)      # 其他，不计入

    # 订单
    _add_order(db, "A1", "paid", 1000, fee=50)        # 未发货
    _add_order(db, "A2", "shipped", 2000, fee=80)     # 待确认收货
    _add_order(db, "A3", "signed", 5000, fee=200)     # 已签收 → 不计入在途
    _add_order(db, "B1", "pending_payment", 300, is_refill=True)  # 待付款刷单

    # 工厂未付
    db.add(FactoryOrder(factory_order_no="F1", payment_status="unpaid",
                        platform_order_no="A1", factory_bill_amount=Decimal("600")))  # 结算费
    db.add(FactoryOrder(factory_order_no="F2", payment_status="unpaid",
                        platform_order_no=None, factory_bill_amount=Decimal("150")))   # 打样费
    db.add(FactoryOrder(factory_order_no="F3", payment_status="paid",
                        platform_order_no="A2", factory_bill_amount=Decimal("999")))   # 已付，不计
    db.flush()

    cash_flow_service.update_manual(db, shop_deposit=Decimal("50000"),
                                    total_investment=Decimal("669871"))
    db.flush()

    s = cash_flow_service.compute_summary(db)
    add = {a["key"]: a["amount"] for a in s["additions"]}
    sub = {x["key"]: x["amount"] for x in s["subtractions"]}

    assert add["shop_deposit"] == Decimal("50000")
    assert add["alipay_balance"] == Decimal("212051.48")  # 207095.16 + 4956.32 (取最新期，不含4月10万)
    assert add["aggregate_balance"] == Decimal("44481.56")
    assert add["promotion_balance"] == Decimal("3553.44")
    assert add["awaiting_receipt"] == Decimal("2000")
    assert add["not_shipped"] == Decimal("1000")

    assert sub["pending_platform_fee"] == Decimal("130")   # 50 + 80 (signed的200不计)
    assert sub["factory_settlement"] == Decimal("600")
    assert sub["factory_sample"] == Decimal("150")
    assert sub["pending_brush"] == Decimal("300")
    assert sub["total_investment"] == Decimal("669871")

    assert s["other_account_balance"] == Decimal("30000")

    expected_add = Decimal("50000") + Decimal("212051.48") + Decimal("44481.56") \
        + Decimal("3553.44") + Decimal("2000") + Decimal("1000")
    expected_sub = Decimal("130") + Decimal("600") + Decimal("150") \
        + Decimal("300") + Decimal("669871")
    assert s["total"] == (expected_add - expected_sub).quantize(Decimal("0.01"))


def test_cash_flow_defaults_empty_db(db_session):
    s = cash_flow_service.compute_summary(db_session)
    # 空库：总投资默认 669871，其余 0 → total = -669871
    assert s["total"] == Decimal("-669871.00")
    assert len(s["freshness"]) >= 1

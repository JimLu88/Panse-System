from decimal import Decimal

from app.models.finance import AccountBalance, RefillRecord
from app.models.order import FactoryOrder, Order
from app.services import cash_flow_service


def _add_balance(db, name, year, month, closing):
    db.add(AccountBalance(
        account_name=name, period_year=year, period_month=month,
        opening_balance=Decimal("0"), income=Decimal("0"),
        expense=Decimal("0"), closing_balance=Decimal(str(closing)),
    ))


def _add_order(db, order_no, status, paid, *, theoretical=None, is_refill=False):
    db.add(Order(
        platform="淘宝", order_no=order_no, status=status,
        paid_amount=Decimal(str(paid)),
        theoretical_cost=None if theoretical is None else Decimal(str(theoretical)),
        is_refill=is_refill,
    ))


def test_cash_flow_summary_formula(db_session):
    db = db_session

    # 账户余额 — 每个账户多期，取最新期；银行卡(其他)也计入可用资金
    _add_balance(db, "支付宝-企业账号", 2026, 4, 100000)
    _add_balance(db, "支付宝-企业账号", 2026, 5, 207095.16)   # 最新
    _add_balance(db, "支付宝-个体户私人", 2026, 5, 4956.32)
    _add_balance(db, "淘宝聚合账户", 2026, 5, 44481.56)
    _add_balance(db, "淘宝推广账户", 2026, 5, 3553.44)
    _add_balance(db, "银行卡-个体户私人", 2026, 5, 30000)      # 其他账户 → 也计入

    # 订单 (活跃单进加项; signed/refill 不进在途)
    _add_order(db, "A1", "paid", 1000)                  # 未发货, 已有工厂账单 → 预估跳过
    _add_order(db, "A2", "shipped", 2000, theoretical=700)  # 待确认收货, 无账单 → 预估 700
    _add_order(db, "A3", "signed", 5000)                # 已签收 → 不计在途
    _add_order(db, "B1", "pending_payment", 300, is_refill=True)  # 补单待付款 → 不计在途

    # 工厂未付账单
    db.add(FactoryOrder(factory_order_no="F1", payment_status="unpaid",
                        platform_order_no="A1", factory_bill_amount=Decimal("600")))  # 已开账单结算
    db.add(FactoryOrder(factory_order_no="F2", payment_status="unpaid",
                        platform_order_no=None, factory_bill_amount=Decimal("150")))   # 打样
    db.add(FactoryOrder(factory_order_no="F3", payment_status="paid",
                        platform_order_no="A3", factory_bill_amount=Decimal("999")))   # 已付 → 不计

    # 代付补单佣金 (未结 = 无支付宝流水号)
    db.add(RefillRecord(order_no="B1", commission=Decimal("80")))
    db.flush()

    cash_flow_service.update_manual(db, shop_deposit=Decimal("50000"),
                                    total_investment=Decimal("669871"))
    db.flush()

    s = cash_flow_service.compute_summary(db)
    add = {a["key"]: a["amount"] for a in s["additions"]}
    sub = {x["key"]: x["amount"] for x in s["subtractions"]}

    # 加项
    assert add["shop_deposit"] == Decimal("50000")
    assert add["alipay_balance"] == Decimal("212051.48")   # 207095.16 + 4956.32 (取最新期)
    assert add["aggregate_balance"] == Decimal("44481.56")
    assert add["promotion_balance"] == Decimal("3553.44")
    assert add["other_balance"] == Decimal("30000")        # 银行卡也计入
    assert add["awaiting_receipt"] == Decimal("2000")
    assert add["not_shipped"] == Decimal("1000")

    # 减项 (新口径)
    assert sub["platform_fee"] == Decimal("18.00")         # (2000+1000)*0.006
    assert sub["factory_sample"] == Decimal("150")
    assert sub["factory_billed"] == Decimal("600")
    assert sub["factory_estimate"] == Decimal("700")       # A2(700); A1 已开账单跳过, 防双算
    assert sub["refill_commission"] == Decimal("80")
    assert "total_investment" not in sub                    # 总投资不再是减项

    # 总投资移出可用资金, 单列投资回收
    inv = s["investment"]
    assert inv["total_investment"] == Decimal("669871")
    assert "total_profit" in inv and "recovery_rate" in inv

    expected_add = (Decimal("50000") + Decimal("212051.48") + Decimal("44481.56")
                    + Decimal("3553.44") + Decimal("30000") + Decimal("2000") + Decimal("1000"))
    expected_sub = Decimal("18.00") + Decimal("150") + Decimal("600") + Decimal("700") + Decimal("80")
    assert s["total"] == (expected_add - expected_sub).quantize(Decimal("0.01"))
    assert s["total"] > 0   # 有账面现金时可用资金必为正 (核心修复)


def test_total_profit_excludes_refill_and_flags_missing_cost(db_session):
    db = db_session
    _add_order(db, "P1", "signed", 1000, theoretical=400)
    _add_order(db, "P2", "shipped", 2000)               # 缺成本 → 计 0 并标记
    _add_order(db, "R1", "signed", 9999, is_refill=True)  # 补单 → 不进利润
    db.flush()
    p = cash_flow_service.compute_total_profit(db)
    assert p["order_count"] == 2                          # 补单被排除
    assert p["orders_missing_cost"] == 1                  # P2 缺成本
    assert p["net_profit"] == Decimal("2600.00")         # (1000-400) + (2000-0)


def test_cash_flow_defaults_empty_db(db_session):
    s = cash_flow_service.compute_summary(db_session)
    # 空库: 总投资不再进减项 → total = 0 (而非 -669871)
    assert s["total"] == Decimal("0.00")
    assert s["investment"]["total_investment"] == Decimal("669871")
    assert len(s["freshness"]) >= 1

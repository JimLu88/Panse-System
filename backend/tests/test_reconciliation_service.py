from datetime import date, datetime
from decimal import Decimal

from app.models.bom import BomLine
from app.models.exception import DataException
from app.models.finance import AlipayFlow, RefillRecord
from app.models.inventory import PartInventory
from app.models.material import Material
from app.models.order import FactoryOrder, Order
from app.services import reconciliation_service as recon


# -------- Rule 1 factory_payment --------

def test_factory_payment_balanced(db_session):
    db_session.add(FactoryOrder(
        factory_order_no="F1", factory_name="博冠", order_date=date(2026, 5, 1),
        qty=1, factory_bill_amount=Decimal("10000"),
    ))
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="T1", transaction_time=datetime(2026, 5, 2),
        counterparty="博冠", amount=Decimal("-10000"),
        reconciliation_type="factory_payment",
    ))
    db_session.flush()
    r = recon.run_factory_payment(db_session, record_exceptions=False)
    assert r.ok_count == 1
    assert r.error_count == 0


def test_factory_payment_underpaid_writes_exception(db_session):
    db_session.add(FactoryOrder(
        factory_order_no="F1", factory_name="博冠", order_date=date(2026, 5, 1),
        qty=1, factory_bill_amount=Decimal("10000"),
    ))
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="T1", transaction_time=datetime(2026, 5, 2),
        counterparty="博冠", amount=Decimal("-9000"),
        reconciliation_type="factory_payment",
    ))
    db_session.flush()
    r = recon.run_factory_payment(db_session, record_exceptions=True)
    boguan = next(d for d in r.diffs if d.key == "博冠")
    assert boguan.diff == Decimal("-1000")
    assert boguan.severity == "error"
    excs = db_session.query(DataException).filter(DataException.source_pk.like("factory_payment:%")).all()
    assert len(excs) == 1


def test_factory_payment_within_tolerance_no_exception(db_session):
    db_session.add(FactoryOrder(
        factory_order_no="F1", factory_name="博冠", order_date=date(2026, 5, 1),
        qty=1, factory_bill_amount=Decimal("10000"),
    ))
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="T1", transaction_time=datetime(2026, 5, 2),
        counterparty="博冠", amount=Decimal("-10003"),  # 差 3 元 < ¥5 阈值
        reconciliation_type="factory_payment",
    ))
    db_session.flush()
    r = recon.run_factory_payment(db_session)
    assert next(d for d in r.diffs if d.key == "博冠").severity == "ok"
    assert db_session.query(DataException).count() == 0


# -------- Rule 7 revenue_alipay 淘金币豁免 (2026-06-21) --------

def _rev_order(db, no, paid):
    db.add(Order(platform="淘宝", order_no=no, qty=1, paid_amount=Decimal(str(paid)),
                 order_date=date(2026, 5, 17), status="signed"))


def test_revenue_alipay_taojinbi_small_positive_exempt(db_session):
    """支付宝该单收入 > 实付 的小额正差(淘金币补贴, 单条付款)→ 不报异常。"""
    _rev_order(db_session, "5115819387933109739", 3576.91)
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="TX1", transaction_time=datetime(2026, 5, 17),
        amount=Decimal("3692.69"), related_order_no="5115819387933109739",
        reconciliation_type="customer_payment"))
    db_session.flush()
    r = recon.run_revenue_alipay(db_session, record_exceptions=True)
    assert not any(d.key == "5115819387933109739" for d in r.diffs)   # 淘金币豁免, 不入异常列表
    assert db_session.query(DataException).filter(
        DataException.source_pk == "revenue_alipay:5115819387933109739").count() == 0


def test_revenue_alipay_refund_diff_netted(db_session):
    """退差价(客户确认收货后退): 订单按净额(实付−退款)对账, 与支付宝该单净收入对平, 不报异常
    (2026-06-23: 旧版只比毛额实付 → 退差价单恒报负差, 现减 refund_amount 后自动平)。"""
    no = "3300165627049005492"
    db_session.add(Order(platform="淘宝", order_no=no, qty=1,
                         paid_amount=Decimal("2711.05"), refund_amount=Decimal("500.00"),
                         order_date=date(2026, 5, 17), status="signed"))
    db_session.add(AlipayFlow(account="企业号", transaction_no="TXR", transaction_time=datetime(2026, 5, 21),
                              amount=Decimal("2211.05"), related_order_no=no,
                              transaction_type="交易付款", reconciliation_type="customer_payment"))
    db_session.flush()
    r = recon.run_revenue_alipay(db_session, record_exceptions=True)
    assert not any(d.key == no and d.severity != "ok" for d in r.diffs)
    assert db_session.query(DataException).filter(
        DataException.source_pk == f"revenue_alipay:{no}").count() == 0


def test_revenue_alipay_platform_coupon_netted(db_session):
    """平台券: 买家应付>实付(实付=扣券净额), 支付宝该单收入=应付 → 用应付对账自动对平, 不报
    (2026-06-23: 平台券是平台出资; 旧版只比实付→恒报正差, 且被同号"收入+分账"误判成重复入库)。"""
    no = "5083434002324457345"
    db_session.add(Order(platform="淘宝", order_no=no, qty=1,
                         paid_amount=Decimal("2723.77"), buyer_payable_amount=Decimal("2928.78"),
                         order_date=date(2026, 5, 17), status="signed"))
    # 担保交易正常的 收入 + 分账(同交易号、不同金额, 不是重复入库)
    db_session.add(AlipayFlow(account="企业号", transaction_no="TXC", transaction_time=datetime(2026, 5, 17),
                              amount=Decimal("2928.78"), related_order_no=no,
                              transaction_type="收入", reconciliation_type="customer_payment"))
    db_session.add(AlipayFlow(account="企业号", transaction_no="TXC", transaction_time=datetime(2026, 6, 1),
                              amount=Decimal("2911.21"), related_order_no=no,
                              transaction_type="交易分账", reconciliation_type="customer_payment"))
    db_session.flush()
    r = recon.run_revenue_alipay(db_session, record_exceptions=True)
    assert not any(d.key == no and d.severity != "ok" for d in r.diffs)
    assert db_session.query(DataException).filter(
        DataException.source_pk == f"revenue_alipay:{no}").count() == 0


def test_revenue_alipay_excludes_payment_accounts_and_empty_orderno(db_session):
    """治本2 (2026-06-23): 货款/采购账户(爱群号/佳宝号/主力号)的正金额流水 + 空订单号流水
    不算营收收入 → 不污染"配不到订单的收入"月度兜底; 真淘宝账户(企业号)照常逐单对账。"""
    # 真实匹配: 企业号客户付款对上订单
    _rev_order(db_session, "5200000000000000001", 1000)
    db_session.add(AlipayFlow(account="企业号", transaction_no="E1", transaction_time=datetime(2026, 5, 17),
                              amount=Decimal("1000.00"), related_order_no="5200000000000000001",
                              reconciliation_type="customer_payment"))
    # 应被排除的污染: 爱群号货款(正金额+合成号)、主力号(正金额)、佳宝号(空订单号)
    db_session.add(AlipayFlow(account="爱群号", transaction_no="1570767231890317011",
                              transaction_time=datetime(2026, 5, 10),
                              amount=Decimal("157129.00"), related_order_no="1570767231890317011",
                              transaction_type="转账", reconciliation_type="customer_payment"))
    db_session.add(AlipayFlow(account="主力号", transaction_no="ZL1", transaction_time=datetime(2026, 5, 11),
                              amount=Decimal("20000.00"), related_order_no="99999999999999999",
                              transaction_type="转账红包", reconciliation_type="customer_payment"))
    db_session.add(AlipayFlow(account="佳宝号", transaction_no="JB1", transaction_time=datetime(2026, 5, 12),
                              amount=Decimal("50000.00"), related_order_no="",
                              transaction_type="转账红包"))
    db_session.flush()
    r = recon.run_revenue_alipay(db_session, record_exceptions=True)
    # 订单对上, 不报; 且没有任何月度兜底异常 (污染流水全被排除)
    assert not any(d.key == "5200000000000000001" and d.severity != "ok" for d in r.diffs)
    assert not any("兜底" in str(d.key) and d.severity != "ok" for d in r.diffs)
    assert db_session.query(DataException).filter(
        DataException.source_table == "reconciliation",
        DataException.source_pk.like("revenue_alipay:%")).count() == 0


def test_revenue_alipay_payable_undercaptured_uses_paid(db_session):
    """应付漏抓(多产品/单品只抓部分子订单): 应付 < 实付 且 实付=支付宝该单收入 →
    对账基准 max(应付,实付)=实付 → 自动对平, 不再误报正差 (2026-06-24)。"""
    no = "5066556590318232018"
    db_session.add(Order(platform="淘宝", order_no=no, qty=1,
                         paid_amount=Decimal("44.00"), buyer_payable_amount=Decimal("22.00"),
                         order_date=date(2026, 5, 17), status="signed"))
    db_session.add(AlipayFlow(account="企业号", transaction_no="TXU", transaction_time=datetime(2026, 5, 17),
                              amount=Decimal("44.00"), related_order_no=no,
                              reconciliation_type="customer_payment"))
    db_session.flush()
    r = recon.run_revenue_alipay(db_session, record_exceptions=True)
    assert not any(d.key == no and d.severity != "ok" for d in r.diffs)
    assert db_session.query(DataException).filter(
        DataException.source_pk == f"revenue_alipay:{no}").count() == 0


def test_revenue_alipay_same_txn_payment_plus_split_counts_once(db_session):
    """同一交易号『交易付款 + 交易分账』是同一笔的支付与结算, 收入取最大额(=付款)只算一次,
    分账不叠加 → 不虚高 (5115065 实例; 同号重复入库另由 alipay_duplicate_flow 规则告警, 2026-06-22)。"""
    no = "9000000000000000001"
    _rev_order(db_session, no, 2837.75)
    db_session.add(AlipayFlow(account="企业号", transaction_no="TXP", transaction_time=datetime(2026, 5, 18),
                              amount=Decimal("2837.75"), related_order_no=no,
                              transaction_type="交易付款", reconciliation_type="customer_payment"))
    db_session.add(AlipayFlow(account="企业号", transaction_no="TXP", transaction_time=datetime(2026, 5, 26),
                              amount=Decimal("278.73"), related_order_no=no,
                              transaction_type="交易分账", reconciliation_type="customer_payment"))
    db_session.flush()
    r = recon.run_revenue_alipay(db_session, record_exceptions=True)
    assert not any(d.key == no and d.severity != "ok" for d in r.diffs)
    assert db_session.query(DataException).filter(
        DataException.source_pk == f"revenue_alipay:{no}").count() == 0


def test_revenue_alipay_different_txn_deposit_and_balance_both_count(db_session):
    """定金 + 尾款 是不同交易号 → 各自保留相加, 不被同号去重误伤(回归)。"""
    no = "9000000000000000003"
    _rev_order(db_session, no, 1500)
    db_session.add(AlipayFlow(account="企业号", transaction_no="DEP", transaction_time=datetime(2026, 5, 10),
                              amount=Decimal("500"), related_order_no=no, reconciliation_type="customer_payment"))
    db_session.add(AlipayFlow(account="企业号", transaction_no="BAL", transaction_time=datetime(2026, 5, 17),
                              amount=Decimal("1000"), related_order_no=no, reconciliation_type="customer_payment"))
    db_session.flush()
    r = recon.run_revenue_alipay(db_session, record_exceptions=True)
    assert not any(d.key == no and d.severity != "ok" for d in r.diffs)   # 500+1000=1500=实付


def test_revenue_alipay_shortfall_still_flags(db_session):
    """支付宝该单收入 < 实付(真短收, 差为负)→ 仍报。"""
    no = "9000000000000000002"
    _rev_order(db_session, no, 1000)
    db_session.add(AlipayFlow(account="企业号", transaction_no="TS1", transaction_time=datetime(2026, 5, 17),
                              amount=Decimal("900"), related_order_no=no,
                              reconciliation_type="customer_payment"))
    db_session.flush()
    r = recon.run_revenue_alipay(db_session, record_exceptions=True)
    assert any(d.key == no and d.severity != "ok" for d in r.diffs)


# -------- Rule 4 refill_compensation --------

def test_refill_no_matching_order(db_session):
    db_session.add(RefillRecord(order_no="X999", total_cost=Decimal("100")))
    db_session.flush()
    r = recon.run_refill_compensation(db_session, record_exceptions=False)
    # 2026-06-11 拍板: 主订单未导入 = 数据缺失提示, 不算差异
    assert r.diffs[0].severity == "not_available"
    assert "主订单未导入" in r.diffs[0].message


def test_refill_matches_order_paid(db_session):
    db_session.add(Order(platform="淘宝", order_no="X1", qty=1, paid_amount=Decimal("100")))
    db_session.add(RefillRecord(order_no="X1", total_cost=Decimal("100")))
    db_session.flush()
    r = recon.run_refill_compensation(db_session)
    assert r.ok_count == 1


def test_refill_mismatch_writes_exception(db_session):
    db_session.add(Order(platform="淘宝", order_no="X1", qty=1, paid_amount=Decimal("80")))
    db_session.add(RefillRecord(order_no="X1", total_cost=Decimal("100")))
    db_session.flush()
    r = recon.run_refill_compensation(db_session)
    diff = r.diffs[0]
    assert diff.diff == Decimal("-20")
    assert diff.severity in ("warning", "error")
    assert db_session.query(DataException).count() == 1


# -------- Rule 4 重做: refill_transfer 刷单对账 --------

def test_refill_transfer_matches_and_flags(db_session):
    """刷单对账: 转徐晶晶(b流水/Y) ↔ 当日补单(Σ订单额/Σ佣金)。"""
    from datetime import datetime, timezone

    from app.models.finance import AlipayFlow
    d = date(2026, 4, 15)
    tt = datetime(2026, 4, 18, tzinfo=timezone.utc)   # 转账日晚于业务日, remark 带业务日
    db_session.add_all([
        RefillRecord(order_no="R1", refill_date=d, order_amount=Decimal("60"), commission=Decimal("10")),
        RefillRecord(order_no="R2", refill_date=d, order_amount=Decimal("40"), commission=Decimal("10")),
    ])
    # 订单额转对(100), 佣金少转(15 vs 应20 → 异常)
    db_session.add_all([
        AlipayFlow(account="t", transaction_no="TX-b", counterparty="徐晶晶",
                   amount=Decimal("-100"), remark="4.15-b流水", transaction_time=tt),
        AlipayFlow(account="t", transaction_no="TX-y", counterparty="徐晶晶",
                   amount=Decimal("-15"), remark="4.15-Y", transaction_time=tt),
    ])
    db_session.flush()
    r = recon.run_refill_transfer(db_session, record_exceptions=False)
    by = {x.key: x for x in r.diffs}
    assert by["2026-04-15-订单额"].severity == "ok"          # 100 == 100
    assert by["2026-04-15-佣金"].severity in ("warning", "error")  # 15 != 20
    assert by["2026-04-15-佣金"].diff == Decimal("-5")


def test_refill_transfer_pending_when_no_transfer(db_session):
    """账上有补单但还没转徐晶晶 → not_available(待转), 不报差错。"""
    db_session.add(RefillRecord(order_no="R9", refill_date=date(2026, 5, 1),
                                order_amount=Decimal("50"), commission=Decimal("10")))
    db_session.flush()
    r = recon.run_refill_transfer(db_session, record_exceptions=False)
    assert all(d.severity == "not_available" for d in r.diffs)


def test_refill_express_annual_suppresses_zero_paid(db_session):
    """补单运费年结口径: 代付台账无实付(实付0) → 不报月差(年结未结) (2026-06-22)。"""
    db_session.add(RefillRecord(order_no="RFX1", refill_date=date(2026, 5, 1), refill_freight=Decimal("615")))
    db_session.add(RefillRecord(order_no="RFX2", refill_date=date(2026, 1, 10), refill_freight=Decimal("240")))
    db_session.flush()
    r = recon.run_refill_express_payout(db_session, record_exceptions=True)
    assert not any(d.severity in ("error", "warning") for d in r.diffs)   # 实付0年结未结, 不报
    assert db_session.query(DataException).filter(
        DataException.source_pk.like("refill_express_payout:%")).count() == 0


# -------- Rule 5 inventory_value --------

def test_inventory_value_basic(db_session):
    db_session.add(Material(code="AC-0001", name="A", price=Decimal("300")))
    db_session.add(Material(code="AC-0002", name="B", price=Decimal("50")))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0001", physical_qty=10, locked_qty=2))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0002", physical_qty=20))
    db_session.flush()
    r = recon.run_inventory_value(db_session)
    total = next(d for d in r.diffs if d.key == "TOTAL")
    # (10-2)*300 + 20*50 = 2400 + 1000 = 3400
    assert total.expected == Decimal("3400.00")


def test_inventory_value_missing_price_flagged(db_session):
    db_session.add(Material(code="AC-1000", name="定制 X", price=None, is_custom=True))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-1000", physical_qty=5))
    db_session.flush()
    r = recon.run_inventory_value(db_session)
    total = next(d for d in r.diffs if d.key == "TOTAL")
    assert total.severity == "warning"
    assert "缺价格" in total.message


# -------- Rule 2/6 not_available --------

def test_install_fee_returns_not_available(db_session):
    r = recon.run_install_fee(db_session)
    assert r.diffs[0].severity == "not_available"


def test_logistics_returns_not_available(db_session):
    r = recon.run_logistics_fee(db_session)
    assert r.diffs[0].severity == "not_available"


def test_promotion_returns_not_available(db_session):
    r = recon.run_promotion(db_session)
    assert r.diffs[0].severity == "not_available"


def test_promotion_recharge_with_flow_no_balances(db_session):
    """改口径: 万相台充值都带充值流水号 → 推广充值 == 有号佐证 → 不报异常 (2026-06-22)。"""
    from app.models.marketing import PromotionFlow
    for d in (5, 12, 19):
        db_session.add(PromotionFlow(transaction_date=date(2026, 6, d), flow_type="充值",
                                     amount=Decimal("5000"), remark="充值 支付宝在线充值",
                                     alipay_flow_no=f"15707{d:02d}000"))
    db_session.flush()
    r = recon.run_promotion(db_session, record_exceptions=True)
    jun = next(d for d in r.diffs if d.key == "2026-06")
    assert jun.expected == Decimal("15000") and jun.actual == Decimal("15000")
    assert jun.severity == "ok"
    assert db_session.query(DataException).filter(
        DataException.source_pk == "promotion:2026-06").count() == 0


def test_promotion_recharge_missing_flow_no_flags(db_session):
    """充值缺充值流水号 → 有号佐证 < 推广充值 → 报差(缺号那笔)。"""
    from app.models.marketing import PromotionFlow
    db_session.add(PromotionFlow(transaction_date=date(2026, 6, 5), flow_type="充值",
                                 amount=Decimal("5000"), remark="充值 支付宝在线充值",
                                 alipay_flow_no="1570700000"))
    db_session.add(PromotionFlow(transaction_date=date(2026, 6, 12), flow_type="充值",
                                 amount=Decimal("5000"), remark="充值 支付宝在线充值",
                                 alipay_flow_no=None))   # 缺号
    db_session.flush()
    r = recon.run_promotion(db_session, record_exceptions=True)
    jun = next(d for d in r.diffs if d.key == "2026-06")
    assert jun.expected == Decimal("10000") and jun.actual == Decimal("5000")
    assert jun.severity != "ok"


# -------- run_all --------

def test_run_all_executes_all_rules(db_session):
    results = recon.run_all(db_session)
    # install_fee 已从 RULES 摘除 (2026-06-17 用户拍板: 充值制不需万师傅月结对账)
    assert "install_fee" not in results
    assert set(results.keys()) == {
        "factory_payment", "promotion",
        "refill_transfer", "inventory_value", "logistics_fee",
        "revenue_alipay", "operating_expense", "purchase_payment",
        # WS4 代付台账三规则 (补单佣金/补单快递/售后 实付↔应摊)
        "refill_commission_payout", "refill_express_payout", "aftersales_payout",
        "refund_reconciliation",
        "ledger_check",   # Rule 14 总账级勾稽 (2026-06-11)
    }


# ── 对账优化③: 账期维度 ───────────────────────────────────────────────────────

def test_refill_compensation_period_filter(db_session):
    """按补单日筛账期: 只对账期内的补单。"""
    db_session.add(RefillRecord(order_no="R-APR", total_cost=Decimal("100"),
                                refill_date=date(2026, 4, 15)))
    db_session.add(RefillRecord(order_no="R-MAY", total_cost=Decimal("100"),
                                refill_date=date(2026, 5, 15)))
    db_session.flush()
    r = recon.run_refill_compensation(
        db_session, period_start=date(2026, 4, 1), period_end=date(2026, 4, 30),
        record_exceptions=False,
    )
    keys = {d.key for d in r.diffs}
    assert "R-APR" in keys
    assert "R-MAY" not in keys
    assert r.period_start == date(2026, 4, 1)


def test_run_all_accepts_period(db_session):
    """run_all 统一透传 period, 所有规则都不报错 (含忽略 period 的库存/物流)。"""
    results = recon.run_all(
        db_session, record_exceptions=False,
        period_start=date(2026, 4, 1), period_end=date(2026, 4, 30),
    )
    assert set(results) == set(recon.RULES)


# -------- Rule 14 ledger_check (总账级勾稽) --------

def test_ledger_check_balanced(db_session):
    """余额变动 = 流水净额 → ok; 账面自洽不另报。"""
    from app.models.finance import AccountBalance
    db_session.add(AccountBalance(
        account_name="企业号", period_year=2026, period_month=3,
        opening_balance=Decimal("10000"), income=Decimal("5000"),
        expense=Decimal("2000"), closing_balance=Decimal("13000"),
    ))
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="L1",
        transaction_time=datetime(2026, 3, 10), amount=Decimal("5000")))
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="L2",
        transaction_time=datetime(2026, 3, 20), amount=Decimal("-2000")))
    db_session.flush()

    r = recon.run_ledger_check(db_session, record_exceptions=False)

    flow = next(d for d in r.diffs if d.key == "企业号 2026-03")
    assert flow.severity == "ok"
    assert flow.diff == Decimal("0")
    # 账面自洽 → 不产生 "账面" 差异行
    assert not any("账面" in d.key for d in r.diffs)


def test_ledger_check_missing_flows_flagged(db_session):
    """余额动了 ¥3000 但当月没导流水 → 差异 + 人话提示。"""
    from app.models.finance import AccountBalance
    db_session.add(AccountBalance(
        account_name="企业号", period_year=2026, period_month=4,
        opening_balance=Decimal("13000"), income=Decimal("3000"),
        expense=Decimal("0"), closing_balance=Decimal("16000"),
    ))
    db_session.flush()

    r = recon.run_ledger_check(db_session, record_exceptions=False)

    flow = next(d for d in r.diffs if d.key == "企业号 2026-04")
    assert flow.severity in ("warning", "error")
    assert "漏导" in flow.message


def test_ledger_check_book_inconsistent_and_exempt(db_session):
    """期初+收-支 ≠ 期末 → 报账面不自洽; 爱群号不做流水勾稽。"""
    from app.models.finance import AccountBalance
    db_session.add(AccountBalance(
        account_name="爱群号", period_year=2026, period_month=3,
        opening_balance=Decimal("1000"), income=Decimal("500"),
        expense=Decimal("0"), closing_balance=Decimal("9999"),  # 账面差 ¥8499
    ))
    db_session.flush()

    r = recon.run_ledger_check(db_session, record_exceptions=False)

    book = next(d for d in r.diffs if "账面" in d.key)
    assert book.severity in ("warning", "error")
    flow = next(d for d in r.diffs if d.key == "爱群号 2026-03")
    assert flow.severity == "not_available"
    assert "豁免" in flow.message

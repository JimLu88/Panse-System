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


# -------- run_all --------

def test_run_all_executes_all_rules(db_session):
    results = recon.run_all(db_session)
    # install_fee 已从 RULES 摘除 (2026-06-17 用户拍板: 充值制不需万师傅月结对账)
    assert "install_fee" not in results
    assert set(results.keys()) == {
        "factory_payment", "promotion",
        "refill_compensation", "inventory_value", "logistics_fee",
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

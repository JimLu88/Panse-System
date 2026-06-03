"""安装费 / 物流费对账 + 资产公式细化 测试."""
from datetime import date, datetime
from decimal import Decimal

from app.models.finance import AlipayFlow, LogisticsBill, WanshifuBill
from app.models.marketing import AfterSales, OutsourcingExpense
from app.models.order import Order
from app.services import asset_service, reconciliation_service


def _install_flow(db, tx, amount, when):
    db.add(AlipayFlow(
        account="企业号", transaction_no=tx, amount=Decimal(str(amount)),
        transaction_time=when, reconciliation_type="install",
    ))
    db.flush()


def _logistics_flow(db, tx, amount, when):
    db.add(AlipayFlow(
        account="企业号", transaction_no=tx, amount=Decimal(str(amount)),
        transaction_time=when, reconciliation_type="logistics",
    ))
    db.flush()


# ----------------------------- 安装费 ----------------------------- #

def test_install_fee_matches_bill_and_flow(db_session):
    """万师傅账单 100 + 支付宝 install 支出 100 → 同月差 0 (ok)."""
    db_session.add(WanshifuBill(bill_date=date(2026, 3, 10), amount=Decimal("100")))
    _install_flow(db_session, "INS1", -100, datetime(2026, 3, 12))

    r = reconciliation_service.run_install_fee(db_session, record_exceptions=False)
    total = next(d for d in r.diffs if d.key == "2026-03")
    assert total.diff == Decimal("0")
    assert total.severity == "ok"


def test_install_fee_flags_mismatch(db_session):
    """账单 100 但实付 20 → 差 -80, 超 ±50 报 error."""
    db_session.add(WanshifuBill(bill_date=date(2026, 3, 10), amount=Decimal("100")))
    _install_flow(db_session, "INS2", -20, datetime(2026, 3, 12))

    r = reconciliation_service.run_install_fee(db_session, record_exceptions=False)
    d = next(d for d in r.diffs if d.key == "2026-03")
    assert d.diff == Decimal("-80")
    assert d.severity == "error"


def test_install_fee_fallback_to_aftersales(db_session):
    """无万师傅账单时, 回退用售后表 wanshifu_deduction 当应付口径."""
    db_session.add(AfterSales(
        platform_order_no="A1", wanshifu_deduction=Decimal("80"),
        processed_at=date(2026, 4, 5),
    ))
    _install_flow(db_session, "INS3", -80, datetime(2026, 4, 6))

    r = reconciliation_service.run_install_fee(db_session, record_exceptions=False)
    d = next(d for d in r.diffs if d.key == "2026-04")
    assert d.expected == Decimal("80")
    assert d.diff == Decimal("0")


def test_install_fee_empty_is_not_available(db_session):
    r = reconciliation_service.run_install_fee(db_session, record_exceptions=False)
    assert r.diffs[0].severity == "not_available"


# ----------------------------- 物流费 ----------------------------- #

def test_logistics_fee_matches_bill_and_flow(db_session):
    db_session.add(LogisticsBill(
        bill_date=date(2026, 3, 1), carrier="德邦", freight_amount=Decimal("200"),
    ))
    _logistics_flow(db_session, "LG1", -200, datetime(2026, 3, 5))

    r = reconciliation_service.run_logistics_fee(db_session, record_exceptions=False)
    d = next(d for d in r.diffs if d.key == "2026-03")
    assert d.diff == Decimal("0")
    assert d.severity == "ok"


def test_logistics_fee_fallback_to_order_freight(db_session):
    """无物流账单时回退用订单 actual_freight."""
    db_session.add(Order(
        platform="淘宝", order_no="O1", qty=1, status="signed",
        order_date=date(2026, 5, 2), actual_freight=Decimal("30"),
    ))
    _logistics_flow(db_session, "LG2", -30, datetime(2026, 5, 3))

    r = reconciliation_service.run_logistics_fee(db_session, record_exceptions=False)
    d = next(d for d in r.diffs if d.key == "2026-05")
    assert d.expected == Decimal("30")
    assert d.diff == Decimal("0")


def test_logistics_fee_empty_is_not_available(db_session):
    r = reconciliation_service.run_logistics_fee(db_session, record_exceptions=False)
    assert r.diffs[0].severity == "not_available"


# ----------------------------- 资产公式细化 ----------------------------- #

def test_asset_breakdown_includes_new_terms(db_session):
    """公式 breakdown 含待确认收货 / 未付平台费 / 未付人员费."""
    # 待确认收货 (shipped)
    db_session.add(Order(
        platform="淘宝", order_no="S1", qty=1, status="shipped",
        paid_amount=Decimal("500"), platform_fee=Decimal("25"),
    ))
    # 未付人员费 (无流水号)
    db_session.add(OutsourcingExpense(payee="张三", amount=Decimal("3000")))
    db_session.flush()

    s = asset_service.summary(db_session)
    assert s.breakdown["待确认收货"] == 500.0
    assert s.breakdown["未付平台费"] == 25.0
    assert s.breakdown["未付人员费"] == 3000.0


def test_run_all_includes_install_and_logistics(db_session):
    """run_all 跑全部规则, 至少含 install_fee / logistics_fee 这两条核心规则."""
    results = reconciliation_service.run_all(db_session, record_exceptions=False)
    assert "install_fee" in results
    assert "logistics_fee" in results
    # 核心六条全在 (允许后续追加更多规则)
    assert {
        "factory_payment", "install_fee", "promotion",
        "refill_compensation", "inventory_value", "logistics_fee",
    }.issubset(set(results))

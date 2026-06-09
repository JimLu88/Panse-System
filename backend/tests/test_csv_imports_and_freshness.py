"""新增 CSV 导入 (售后/推广/补单/账户余额) + 数据新鲜度提醒 测试."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.models.finance import AccountBalance, AlipayFlow, RefillRecord
from app.models.marketing import AfterSales, PromotionFlow
from app.models.order import Order
from app.services import bill_import_service, data_freshness_service


# ----------------------------- CSV 导入 ----------------------------- #

def test_import_aftersales_csv(db_session):
    csv = "订单号,售后原因,万师傅扣款,处理日期\nA100,安装损坏,80,2026-05-10\n,空订单跳过,5,2026-05-11\n"
    r = bill_import_service.import_aftersales_csv(db_session, csv)
    assert r.inserted == 1
    assert r.skipped_invalid == 1
    a = db_session.query(AfterSales).filter_by(platform_order_no="A100").one()
    assert a.wanshifu_deduction == Decimal("80")
    assert a.processed_at == date(2026, 5, 10)


def test_import_promotion_flows_csv(db_session):
    csv = "日期,类型,金额,备注\n2026-05-01,支出,1200,直通车\n2026-05-02,,,缺金额跳过\n"
    r = bill_import_service.import_promotion_flows_csv(db_session, csv)
    assert r.inserted == 1
    assert r.skipped_invalid == 1
    p = db_session.query(PromotionFlow).one()
    assert p.amount == Decimal("1200")
    assert p.flow_type == "支出"


def test_import_refill_records_csv(db_session):
    csv = "订单号,补单日期,数量,总成本\nR1,2026-05-05,2,150.50\n,2026-05-06,1,10\n"
    r = bill_import_service.import_refill_records_csv(db_session, csv)
    assert r.inserted == 1
    assert r.skipped_invalid == 1
    rec = db_session.query(RefillRecord).filter_by(order_no="R1").one()
    assert rec.qty == 2
    assert rec.total_cost == Decimal("150.50")


def test_import_account_balances_csv_upsert(db_session):
    csv1 = "账户名,年,月,期末余额\n企业号,2026,4,5000\n"
    bill_import_service.import_account_balances_csv(db_session, csv1)
    # 再导一次同账户同月 → upsert 更新而非新增
    csv2 = "账户名,年,月,期末余额\n企业号,2026,4,8000\n"
    bill_import_service.import_account_balances_csv(db_session, csv2)
    rows = db_session.query(AccountBalance).filter_by(account_name="企业号").all()
    assert len(rows) == 1
    assert rows[0].closing_balance == Decimal("8000")


def test_account_balances_csv_skips_invalid(db_session):
    csv = "账户名,年,月,期末余额\n,2026,4,100\n企业号,,4,200\n企业号,2026,5,300\n"
    r = bill_import_service.import_account_balances_csv(db_session, csv)
    assert r.inserted == 1
    assert r.skipped_invalid == 2


# ----------------------------- 数据新鲜度 ----------------------------- #

def test_freshness_all_sources_present(db_session):
    """check_all 返回全部数据源 (含新增 代付台账 / 微信账单)."""
    items = data_freshness_service.check_all(db_session)
    sources = {i.source for i in items}
    assert sources == {
        "支付宝流水", "万师傅安装账单", "物流费账单", "推广记录",
        "账户余额", "淘宝订单", "补单对账", "售后表",
        "代付台账", "微信账单",
    }


def test_freshness_flags_stale_alipay(db_session):
    """无任何支付宝流水 → 标记过期."""
    items = {i.source: i for i in data_freshness_service.check_all(db_session)}
    assert items["支付宝流水"].overdue is True
    assert items["支付宝流水"].days_stale == 9999


def test_freshness_fresh_order_not_overdue(db_session):
    """昨天有订单 → 淘宝订单不过期."""
    db_session.add(Order(
        platform="淘宝", order_no="FRESH1", qty=1, status="paid",
        order_date=date.today() - timedelta(days=1),
    ))
    db_session.flush()
    items = {i.source: i for i in data_freshness_service.check_all(db_session)}
    assert items["淘宝订单"].overdue is False


def test_freshness_recent_flow_not_overdue(db_session):
    db_session.add(AlipayFlow(
        account="企业号", transaction_no="TX1", amount=Decimal("100"),
        transaction_time=datetime.now(),
    ))
    db_session.flush()
    items = {i.source: i for i in data_freshness_service.check_all(db_session)}
    assert items["支付宝流水"].overdue is False


def test_check_and_remind_returns_counts(db_session):
    """空库时所有月度源过期 → reminded 计数 > 0 (notify 未配置时静默)."""
    res = data_freshness_service.check_and_remind(db_session)
    assert res["overdue"] >= 1
    assert res["reminded"] == res["overdue"]

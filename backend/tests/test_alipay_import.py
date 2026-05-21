from decimal import Decimal

from app.models.finance import AlipayFlow
from app.services import alipay_import


def test_import_basic_alipay_csv(db_session):
    csv_text = (
        "交易时间,交易流水号,交易类型,交易对象,收支金额,备注\n"
        "2026-04-28 10:00:00,T001,在线支付,买家1,127.00,订单收款\n"
        "2026-04-28 11:00:00,T002,分账,淘宝,-0.76,基础服务费\n"
    )
    r = alipay_import.import_alipay_csv(db_session, csv_text, account="企业号")
    assert r.inserted == 2
    rows = db_session.query(AlipayFlow).order_by(AlipayFlow.transaction_no).all()
    assert rows[0].amount == Decimal("127.00")
    assert rows[1].amount == Decimal("-0.76")
    assert all(r.account == "企业号" for r in rows)


def test_import_dedup_per_account(db_session):
    csv_text = "交易时间,交易流水号,收支金额\n2026-04-28,T001,100\n"
    alipay_import.import_alipay_csv(db_session, csv_text, account="企业号")
    r = alipay_import.import_alipay_csv(db_session, csv_text, account="企业号")
    assert r.inserted == 0
    assert r.skipped_duplicate == 1
    # 但换账户可以重复
    r = alipay_import.import_alipay_csv(db_session, csv_text, account="私账")
    assert r.inserted == 1


def test_import_skips_invalid(db_session):
    csv_text = "交易时间,交易流水号,收支金额\n2026-04-28,,100\n2026-04-28,T001,bad-num\n2026-04-28,T002,200\n"
    r = alipay_import.import_alipay_csv(db_session, csv_text, account="企业号")
    assert r.inserted == 1
    assert r.skipped_invalid == 2


def test_import_requires_required_columns(db_session):
    r = alipay_import.import_alipay_csv(db_session, "交易时间\n2026-04-28\n", account="企业号")
    assert r.inserted == 0
    assert any("交易流水号" in e for e in r.errors)

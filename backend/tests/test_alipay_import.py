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


def test_import_personal_alipay_csv(db_session):
    """个人号交易记录明细(consumeprod 高级查询下载, 2026-07-06): 前导/页脚跳过,
    金额（元）无符号 + 收/支 合成带符号(支出负/收入正/不计收支正), 自动识别路由。"""
    csv_text = (
        "支付宝交易记录明细查询\n"
        "账号:[15824198812]\n"
        "起始日期:[2026-06-30 00:00:00]    终止日期:[2026-07-06 01:18:59]\n"
        "---------------------------------交易记录明细列表---------------------------------\n"
        "交易号,商家订单号,交易创建时间,付款时间,最近修改时间,交易来源地,类型,交易对方,商品名称,"
        "金额（元）,收/支,交易状态,服务费（元）,成功退款（元）,备注,资金状态,\n"
        "2026070500001,P100,2026-07-05 17:01:00,2026-07-05 17:01:00,,,转账,义乌畔色贸易,货款,"
        "10000.00,收入,交易成功,0.00,0.00,,,\n"
        "2026070500002,,2026-07-05 16:46:00,2026-07-05 16:46:00,,,支付,万师傅,安装费,"
        "79.99,支出,交易成功,0.00,0.00,,,\n"
        "2026070500003,,2026-07-05 12:00:00,,,,转入,余额宝,,"
        "5000.00,不计收支,交易成功,0.00,0.00,,,\n"
        "------------------------------------------------------------\n"
        "已收入:1笔,10000.00元\n"
        "已支出:1笔,79.99元\n"
        "导出时间:[2026-07-06 01:18:59]    用户:陆振达\n"
    )
    r = alipay_import.import_alipay_csv(db_session, csv_text, account="主力号")
    assert r.inserted == 3                                   # 3 数据行(前导+页脚都跳过)
    rows = {x.transaction_no: x for x in db_session.query(AlipayFlow).all()}
    assert rows["2026070500001"].amount == Decimal("10000.00")   # 收入 → 正
    assert rows["2026070500002"].amount == Decimal("-79.99")     # 支出 → 负
    assert rows["2026070500003"].amount == Decimal("5000.00")    # 不计收支 → 正(中性)
    assert rows["2026070500002"].counterparty == "万师傅"
    assert rows["2026070500001"].related_order_no == "P100"
    assert all(x.account == "主力号" for x in rows.values())


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


def test_import_keeps_paired_flows_same_txn_no(db_session):
    """同一交易流水号下『在线支付(收款)+分账(手续费)』为正常配对, 两条都要入库。"""
    csv_text = (
        "交易时间,交易流水号,交易类型,收支金额,备注\n"
        "2026-04-28 10:00:00,T100,在线支付,127.00,客户收款\n"
        "2026-04-28 10:00:00,T100,分账,-0.76,淘宝手续费\n"
    )
    r = alipay_import.import_alipay_csv(db_session, csv_text, account="企业号")
    assert r.inserted == 2
    assert r.skipped_duplicate == 0
    rows = db_session.query(AlipayFlow).filter(AlipayFlow.transaction_no == "T100").all()
    assert {row.transaction_type for row in rows} == {"在线支付", "分账"}


def test_import_dedup_only_when_type_and_amount_match(db_session):
    """同号 + 同类型 + 同金额 才算真重复; 同号同类型不同金额仍入库。"""
    csv_text = (
        "交易时间,交易流水号,交易类型,收支金额\n"
        "2026-04-28,T200,分账,-0.13\n"
        "2026-04-28,T200,分账,-0.13\n"   # 完全相同 → 去重
        "2026-04-28,T200,分账,-35.15\n"  # 同号同类型但金额不同 → 保留
    )
    r = alipay_import.import_alipay_csv(db_session, csv_text, account="企业号")
    assert r.inserted == 2
    assert r.skipped_duplicate == 1


def test_import_dedup_normalizes_fenzhang_label(db_session):
    """『分账』与『交易分账』是同一笔结算的不同标签 — 同号同额视为重复, 只入一条 (2026-06-22)。"""
    csv_text = (
        "交易时间,交易流水号,交易类型,收支金额\n"
        "2026-04-28,T300,分账,2658.36\n"
        "2026-04-28,T300,交易分账,2658.36\n"   # 同号同额, 仅标签不同 → 去重
    )
    r = alipay_import.import_alipay_csv(db_session, csv_text, account="企业号")
    assert r.inserted == 1
    assert r.skipped_duplicate == 1


def test_import_dedup_normalizes_across_separate_imports(db_session):
    """先导『分账』, 再导同号同额『交易分账』(另一次导入) — DB 存在性检查也去重。"""
    alipay_import.import_alipay_csv(
        db_session, "交易时间,交易流水号,交易类型,收支金额\n2026-04-28,T301,分账,99.00\n", account="企业号")
    r = alipay_import.import_alipay_csv(
        db_session, "交易时间,交易流水号,交易类型,收支金额\n2026-04-28,T301,交易分账,99.00\n", account="企业号")
    assert r.inserted == 0
    assert r.skipped_duplicate == 1

"""表格桥接(CSV/xlsx 通吃) + 类型识别(文件名+表头) + 路由入库。"""
import io
from datetime import datetime

import openpyxl

from app.models.finance import LogisticsBill, WanshifuBill
from app.services import table_ingest_service as tis, tabular


def _xlsx(headers, *rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(list(r))
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def test_tabular_csv_passthrough():
    raw = "日期,运费\n2026-05-01,88\n".encode("utf-8-sig")
    assert "2026-05-01" in tabular.to_csv_text(raw, "x.csv")


def test_tabular_xlsx_to_csv_with_dates():
    content = _xlsx(["日期", "运费"], [datetime(2026, 5, 1), 88.5])
    txt = tabular.to_csv_text(content, "x.xlsx")
    assert "日期,运费" in txt
    assert "2026-05-01" in txt           # 日期被格式化成 ISO, 不是序列号
    assert tabular.read_header(content, "x.xlsx") == ["日期", "运费"]


def test_xlsx_large_integer_id_not_scientific():
    # 被 Excel 数字化的长订单号: 不能写成科学计数, 否则污染去重键
    content = _xlsx(["订单号", "金额"], [123456789012, 100.0])
    txt = tabular.to_csv_text(content, "x.xlsx")
    assert "123456789012" in txt
    assert "e+" not in txt.lower() and "E+" not in txt
    assert "100" in txt and "100.0" not in txt   # 整数金额不带 .0


def test_classify_kuaidi_daifu_goes_prepay():
    # "快递代付台账" 不再被 logistics 的"快递"抢走 → 走代付台账
    assert tis.classify_table("快递代付台账.xlsx", b"") == "prepay"
    assert tis.classify_table("物流月结账单.xlsx", b"") == "logistics"


def test_alipay_import_commit_false_is_atomic(db_session):
    from app.models.finance import AlipayFlow
    from app.services import alipay_import
    db = db_session
    csv_text = "交易流水号,收支金额,交易类型\nT100,-50,采购\n"
    rep = alipay_import.import_alipay_csv(db, csv_text, account="企业号", commit=False)
    assert rep.inserted == 1
    db.rollback()   # commit=False → 调用方可回滚, 不会留下半条
    assert db.query(AlipayFlow).filter_by(transaction_no="T100").count() == 0


def test_legacy_xls_rejected():
    # OLE 头伪装成 .xls
    try:
        tabular.to_csv_text(b"\xd0\xcf\x11\xe0rest", "old.xls")
        assert False
    except ValueError as e:
        assert "xlsx" in str(e)


def test_classify_by_filename_and_header():
    # 文件名关键词
    assert tis.classify_table("企业号支付宝流水.xlsx", b"") == "alipay"
    assert tis.classify_table("补单对账.xlsx", b"") == "refill"        # 不被"对账"误判成工厂
    assert tis.classify_table("工厂对账单.xlsx", b"") == "factory_recon"
    # 文件名无关键词 → 看表头指纹
    ws = _xlsx(["日期", "服务类型", "订单号", "金额", "状态"], [datetime(2026, 5, 1), "安装", "O1", 100, "完成"])
    assert tis.classify_table("xyz.xlsx", ws) == "wanshifu"


def test_import_table_logistics_xlsx(db_session):
    db = db_session
    content = _xlsx(["日期", "承运商", "运单号", "订单号", "重量(kg)", "运费", "备注"],
                    [datetime(2026, 5, 1), "德邦", "DB1", "O1", 12.0, 88.5, ""])
    r = tis.import_table(db, "logistics", content, "物流账单.xlsx")
    db.flush()
    assert r["ok"] is True
    assert db.query(LogisticsBill).count() == 1


def test_import_table_wanshifu_xlsx(db_session):
    db = db_session
    content = _xlsx(["日期", "订单号", "服务类型", "金额", "状态", "备注"],
                    [datetime(2026, 5, 2), "O2", "安装", 150, "完成", ""])
    r = tis.import_table(db, "wanshifu", content, "万师傅.xlsx")
    db.flush()
    assert r["ok"] is True
    assert db.query(WanshifuBill).count() == 1

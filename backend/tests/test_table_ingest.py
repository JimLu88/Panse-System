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

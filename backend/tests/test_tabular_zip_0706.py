"""tabular: 普通 zip(内含 CSV, 如支付宝个人账单下载的 .zip) 直接被导入口解出入库 (用户 2026-07-06)。"""
import io
import zipfile

from app.services import tabular


def _zip_of(csv_bytes: bytes, name: str = "alipay_record_1.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv_bytes)
    return buf.getvalue()


def test_to_csv_text_unzips_plain_zip():
    csv = "交易号,金额（元）,收/支\n2026060100001,100.00,支出\n".encode("gbk")
    out = tabular.to_csv_text(_zip_of(csv), "alipay_record_20260706.zip")
    assert "交易号" in out and "2026060100001" in out


def test_to_csv_text_plain_csv_unaffected():
    out = tabular.to_csv_text("a,b\n1,2\n".encode("utf-8"), "x.csv")
    assert "a,b" in out and "1,2" in out


def test_to_csv_text_xlsx_not_treated_as_data_zip():
    """xlsx 本身是 zip, 但有 [Content_Types].xml/xl/ → 不当'内含csv的zip'解, 仍走 xlsx 分支。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["列1", "列2"])
    ws.append(["v1", "v2"])
    buf = io.BytesIO()
    wb.save(buf)
    out = tabular.to_csv_text(buf.getvalue(), "x.xlsx")
    assert "列1" in out and "v1" in out


def test_alipay_zip_routes_to_personal_import(db_session):
    """支付宝个人账单 zip → to_csv_text 解出 → import_alipay_csv 自动识别个人格式入库。"""
    from app.services import alipay_import
    csv = (
        "支付宝交易记录明细查询\n账号:[x]\n"
        "交易号,商家订单号,交易创建时间,付款时间,最近修改时间,交易来源地,类型,交易对方,商品名称,"
        "金额（元）,收/支,交易状态,服务费（元）,成功退款（元）,备注,资金状态,\n"
        "2026060100001,,2026-06-01 10:00:00,,,,支付,万师傅,安装费,79.99,支出,交易成功,0,0,,,\n"
    ).encode("gbk")
    text = tabular.to_csv_text(_zip_of(csv), "alipay_record.zip")
    r = alipay_import.import_alipay_csv(db_session, text, account="主力号")
    assert r.inserted == 1

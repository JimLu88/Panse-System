# -*- coding: utf-8 -*-
"""淘宝订单多格式自动识别导入测试。"""
import csv
import io

from openpyxl import Workbook

from app.models.order import Order
from app.services import taobao_order_import as tio


# ── 单元: 字段转换 ────────────────────────────────────────────────────────────
def test_product_code_from_merchant():
    assert tio.product_code_from_merchant("PPS2638004022511") == "P26380040225"
    assert tio.product_code_from_merchant("23210020201") == "P23210020201"
    assert tio.product_code_from_merchant("") == ""
    assert tio.product_code_from_merchant(None) == ""
    # 服务类哈希编码 → 非 11 位数字 → 空
    assert tio.product_code_from_merchant("de8df067567b3da9adb3a1d4d1918334") == ""


def test_extract_sku():
    assert tio.extract_sku("颜色分类:榉木床头柜-标准[长45cm];安装方式:免安装") == "榉木床头柜-标准"
    assert tio.extract_sku("颜色分类：砂白色2.0米岩板餐桌") == "砂白色2.0米岩板餐桌"
    assert tio.extract_sku("") == ""


def test_status_map():
    assert tio._map_status("交易成功") == "signed"
    assert tio._map_status("买家已付款,等待卖家发货") == "paid"
    assert tio._map_status("卖家已发货，等待买家确认") == "shipped"
    assert tio._map_status("交易关闭") == "cancelled"
    assert tio._map_status("等待买家付款") == "pending_payment"


# ── 格式识别 ──────────────────────────────────────────────────────────────────
def _qianniu_xlsx_bytes() -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    o = wb.create_sheet("订单报表")
    o.append(["订单编号", "买家应付货款", "订单状态", "收货地址", "物流单号", "物流公司", "订单创建时间"])
    o.append(["A100", "2104.80", "买家已付款,等待卖家发货", "浙江省杭州市", "YT123", "圆通", "2026-06-02 11:37:39"])
    s = wb.create_sheet("销售明细")
    s.append(["子订单编号", "主订单编号", "商品标题", "购买数量", "商家编码", "商品属性", "买家应付货款", "退款状态", "退款金额", "订单创建时间"])
    s.append(["A100", "A100", "畔色榉木床头柜", "2", "PPS2638004022511", "颜色分类:榉木床头柜-标准[长45cm]", "2104.80", "没有申请退款", "无退款申请", "2026-06-02 11:37:39"])
    f = wb.create_sheet("发货报表")
    f.append(["订单编号", "收货人姓名", "联系手机", "收货地址"])
    f.append(["A100", "张三", "13800138000", "浙江省杭州市滨江区"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sales_detail_csv_bytes() -> bytes:
    rows = [
        ["子订单编号", "主订单编号", "标题", "价格", "购买数量", "外部系统编号",
         "商品属性", "订单状态", "商家编码", "买家应付货款", "退款状态", "退款金额", "订单创建时间"],
        ["B200", "B200", "畔色岩板餐桌", "5100.00", "1", "23210020201",
         "颜色分类：砂白色2.0米岩板餐桌", "交易成功", "23210020201", "2933.78", "没有申请退款", "无退款申请", "2026-01-18 13:39:27"],
    ]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("gbk")


def test_detect_qianniu_multi():
    assert tio.detect_format("export.xlsx", _qianniu_xlsx_bytes()) == "qianniu_multi"


def test_detect_sales_detail_csv():
    assert tio.detect_format("ItemList.csv", _sales_detail_csv_bytes()) == "sales_detail"


def test_detect_unknown():
    bad = "甲,乙,丙\n1,2,3\n".encode("utf-8")
    assert tio.detect_format("x.csv", bad) == "unknown"


# ── 端到端导入 ────────────────────────────────────────────────────────────────
def test_import_qianniu_multi(db_session):
    rep = tio.import_taobao_orders(db_session, "export.xlsx", _qianniu_xlsx_bytes())
    assert rep.detected_format == "qianniu_multi"
    assert rep.inserted == 1
    o = db_session.query(Order).filter_by(order_no="A100").one()
    assert o.product_code == "P26380040225"     # PPS→P 还原
    assert o.sku == "榉木床头柜-标准"             # 商品属性提取
    assert o.sku_code == "PPS2638004022511"
    assert o.qty == 2
    assert o.customer_name == "张三"             # 发货报表关联
    assert o.customer_phone == "13800138000"
    assert o.carrier == "圆通"                   # 订单报表关联
    assert o.status == "paid"
    assert o.is_historical is True


def test_import_sales_detail_csv(db_session):
    rep = tio.import_taobao_orders(db_session, "ItemList.csv", _sales_detail_csv_bytes())
    assert rep.detected_format == "sales_detail"
    assert rep.inserted == 1
    o = db_session.query(Order).filter_by(order_no="B200").one()
    assert o.product_code == "P23210020201"
    assert o.sku == "砂白色2.0米岩板餐桌"
    assert o.status == "signed"


def test_import_dedup(db_session):
    raw = _sales_detail_csv_bytes()
    tio.import_taobao_orders(db_session, "ItemList.csv", raw)
    rep2 = tio.import_taobao_orders(db_session, "ItemList.csv", raw)
    assert rep2.inserted == 0
    assert rep2.skipped_duplicate == 1


def test_import_multi_line_order(db_session):
    """一单多商品: 聚合为一行 Order, 取主商品(金额最大), 备注其余。"""
    wb = Workbook(); wb.remove(wb.active)
    s = wb.create_sheet("销售明细")
    s.append(["子订单编号", "主订单编号", "商品标题", "购买数量", "商家编码", "商品属性", "买家应付货款", "订单创建时间"])
    s.append(["C1", "C300", "送货入户", "1", "", "", "0.00", "2026-01-06 19:36:09"])
    s.append(["C2", "C300", "畔色实木餐边柜", "1", "23250050202", "颜色分类：其他尺寸定制", "11212.00", "2026-01-06 19:36:09"])
    buf = io.BytesIO(); wb.save(buf)
    rep = tio.import_taobao_orders(db_session, "x.xlsx", buf.getvalue())
    assert rep.inserted == 1
    assert rep.multi_line_orders == 1
    o = db_session.query(Order).filter_by(order_no="C300").one()
    assert o.product_name == "畔色实木餐边柜"   # 金额最大行为主
    assert "本单含2个商品" in (o.remark or "")

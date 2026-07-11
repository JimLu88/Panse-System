# -*- coding: utf-8 -*-
"""「已卖出的宝贝」漏网格式治本 (2026-07-11): 多商品订单一单多行、但表头没有子订单编号列 →
表头判据判成"单级权威", 每晚与订单报表互搏(8单实付/状态/退款反复横跳)。
内容级判据: 同文件订单号重复≥2次 → 按行级处理, 不覆盖已存在订单的财务/状态。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
import io
from datetime import date
from decimal import Decimal as D

from openpyxl import Workbook

from app.models.order import Order
from app.services.taobao_order_import import import_taobao_orders


def _wb_bytes(header, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "export"
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_HDR = ["订单编号", "订单状态", "买家实付金额", "退款金额", "商品名称"]


def test_repeat_orderno_file_treated_as_line(db_session):
    """订单号重复的文件(已卖出宝贝式) → 行级: 不覆盖已存在订单的 实付/状态/退款。"""
    db_session.add(Order(platform="淘宝", order_no="3307941483418122285", qty=1,
                         status="signed", paid_amount=D("4500.36"),
                         refund_amount=D("1795.17"), order_date=date(2026, 6, 20)))
    db_session.flush()
    raw = _wb_bytes(_HDR, [
        ["3307941483418122285", "等待卖家发货", 2705.19, 0, "岩板餐桌"],
        ["3307941483418122285", "等待卖家发货", 1795.17, 0, "床头柜"],
        ["3300000000000000001", "等待卖家发货", 100, 0, "样块"],
    ])
    rep = import_taobao_orders(db_session, "ExportOrderList999.xlsx", raw)
    assert any("订单号重复" in w for w in rep.warnings)
    o = db_session.query(Order).filter_by(order_no="3307941483418122285").one()
    assert o.paid_amount == D("4500.36")     # 不被行级值盖掉
    assert o.status == "signed"
    assert o.refund_amount == D("1795.17")


def test_unique_orderno_file_stays_order_level(db_session):
    """订单号不重复的单级导出照旧权威: 正常覆盖更新(回归)。"""
    db_session.add(Order(platform="淘宝", order_no="3300000000000000900", qty=1,
                         status="paid", paid_amount=D("50"), order_date=date(2026, 6, 20)))
    db_session.flush()
    raw = _wb_bytes(_HDR, [
        ["3300000000000000900", "交易成功", 100, 0, "餐桌"],
        ["3300000000000000901", "等待卖家发货", 200, 0, "书柜"],
    ])
    rep = import_taobao_orders(db_session, "ExportOrderList998.xlsx", raw)
    assert not any("订单号重复" in w for w in rep.warnings)
    o = db_session.query(Order).filter_by(order_no="3300000000000000900").one()
    assert o.paid_amount == D("100")         # 单级权威, 照常刷新
    assert o.status == "signed"              # 交易成功 → signed
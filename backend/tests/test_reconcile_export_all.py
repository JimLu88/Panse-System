# -*- coding: utf-8 -*-
"""逐单核对 多月公式导出 (用户 2026-06-25): 每月一 sheet, 派生值用 Excel 公式, 兜底单商品成本=公式。"""
from datetime import date
from decimal import Decimal

from app.api.reports import _build_reconcile_workbook_all
from app.models.order import Order


def test_export_all_month_sheet_and_formulas(db_session):
    # 推演封顶单: 实付550, 推演=定价467.5+打包170=637.5 > 550 → 商品成本走 实付×85%
    db_session.add(Order(platform="淘宝", order_no="X1", status="paid", is_refill=False,
                         order_date=date(2026, 6, 5), paid_amount=Decimal("550"),
                         theoretical_cost=Decimal("467.5"), est_packing=Decimal("170"),
                         is_custom=True, product_name="测试下柜"))
    db_session.flush()

    wb = _build_reconcile_workbook_all(db_session, [(2026, 6)])
    assert "2026-06" in wb.sheetnames
    ws = wb["2026-06"]
    # 表头在第2行, 数据从第3行
    assert ws["F2"].value == "真实收入"
    assert ws["M2"].value == "成本合计"
    assert ws["N2"].value == "净利"
    # 派生值是 Excel 公式(可回推), 不是数值
    assert str(ws["F3"].value).startswith("=")        # 真实收入=D-E
    assert str(ws["M3"].value).startswith("=")        # 成本合计=ΣG..L
    assert str(ws["N3"].value).startswith("=")        # 净利=F-M
    assert str(ws["O3"].value).startswith("=IF")      # 净利率
    # 兜底单: 商品成本写成 实付×85% 公式
    assert str(ws["G3"].value) == "=D3*0.85"
    assert ws["P3"].value.startswith("实付×85%")      # 成本来源标注


def test_export_all_empty_period_has_placeholder(db_session):
    wb = _build_reconcile_workbook_all(db_session, [(2025, 1)])
    assert "无数据" in wb.sheetnames

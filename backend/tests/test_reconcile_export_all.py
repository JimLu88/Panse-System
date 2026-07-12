# -*- coding: utf-8 -*-
"""逐单核对 多月公式导出 (用户 2026-06-25): 每月一 sheet; 每个金额可公式回推; 商品成本拆解=工厂木作+定价估算+打包(或实付×85%)。"""
from datetime import date
from decimal import Decimal

from app.api.reports import _build_reconcile_workbook_all
from app.models.order import Order


def test_export_all_capped_order_formulas(db_session):
    # 推演封顶单: 实付550, 推演=定价467.5+打包170=637.5 > 550 → 商品成本走 实付×85%
    db_session.add(Order(platform="淘宝", order_no="X1", status="paid", is_refill=False,
                         order_date=date(2026, 6, 5), paid_amount=Decimal("550"),
                         theoretical_cost=Decimal("467.5"), est_packing=Decimal("170"),
                         is_custom=True, product_name="测试下柜"))
    db_session.flush()

    wb = _build_reconcile_workbook_all(db_session, [(2026, 6)])
    assert "2026-06" in wb.sheetnames
    ws = wb["2026-06"]
    # 表头(第2行)定位
    assert ws["F2"].value == "真实收入"
    assert ws["J2"].value == "商品成本"
    assert ws["P2"].value == "成本合计"
    assert ws["Q2"].value == "净利"
    assert ws["R2"].value == "净利率"
    # 派生值是 Excel 公式(可回推); 例外: 成本合计直写数值 (2026-07-12 用户定版 ——
    # 物流/安装列改为展示值(含折在商品成本里的分量), 公式 J+K+L+… 会重复计, 故 P 落实值)
    assert str(ws["F3"].value) == "=D3-E3"             # 真实收入=实付−退款
    assert isinstance(ws["P3"].value, float)            # 成本合计=数值(见上)
    assert str(ws["Q3"].value) == "=F3-P3"              # 净利=收入−成本合计
    assert str(ws["R3"].value).startswith("=IF")        # 净利率
    # 兜底单: 商品成本=实付×85% 公式, 来源标注橙
    assert str(ws["J3"].value) == "=D3*0.85"
    assert ws["S3"].value.startswith("实付×85%")
    # 值单元格必须落在正确列(防 _money 行列错位回归): 实付D / 工厂木作G / 定价估算H / 打包I
    assert ws["D3"].value == 550.0     # 实付
    assert ws["G3"].value == 0.0       # 工厂木作账单(无账单→0)
    assert ws["H3"].value == 467.5     # 定价表估算(=theoretical, 推演)
    assert ws["I3"].value == 170.0     # 打包


def test_export_all_normal_order_goods_is_sum_formula(db_session):
    # 推演单(成本<实付, 不封顶): 商品成本 = 工厂木作 + 定价估算 + 打包 公式
    db_session.add(Order(platform="淘宝", order_no="Y1", status="paid", is_refill=False,
                         order_date=date(2026, 5, 4), paid_amount=Decimal("3000"),
                         theoretical_cost=Decimal("1000"), product_name="测试桌"))
    db_session.flush()
    wb = _build_reconcile_workbook_all(db_session, [(2026, 5)])
    ws = wb["2026-05"]
    assert str(ws["J3"].value) == "=G3+H3+I3"   # 商品成本=工厂木作+定价估算+打包
    assert ws["S3"].value == "定价表推演"
    assert ws["D3"].value == 3000.0    # 实付 落在 D 列(防错位)
    assert ws["H3"].value == 1000.0    # 定价表估算=theoretical


def test_export_all_empty_period_has_placeholder(db_session):
    wb = _build_reconcile_workbook_all(db_session, [(2024, 1)])
    assert "无数据" in wb.sheetnames

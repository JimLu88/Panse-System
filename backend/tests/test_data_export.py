# -*- coding: utf-8 -*-
"""全量导出大改回归: 产品总表按SKU展开 + VLOOKUP公式 + 毛利率验算 + 数字格式 + 不崩。"""
from decimal import Decimal

from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import data_export_service as dx


def _seed(db):
    db.add(Product(code="P100", name="测试餐桌", category="餐桌", brand="畔色",
                   listing_status="在售", priority="high", main_material="岩板",
                   aux_material="实木"))
    db.add(Product(code="P200", name="无SKU产品", category="床", brand="畔色",
                   listing_status="下架", priority="low"))
    db.add(PricingSku(product_code="P100", sku_code="P100-A", sku="1.4米",
                      size_category="mid", accounting_cost=Decimal("2000.00"),
                      gross_margin_rate=Decimal("0.35"), big_promo=Decimal("3076.92"),
                      daily_price=Decimal("3500.00"), factory_cost=Decimal("1500.00"),
                      wood_cost=Decimal("800.00")))
    db.add(PricingSku(product_code="P100", sku_code="P100-B", sku="1.6米",
                      size_category="large", accounting_cost=Decimal("2400.00"),
                      gross_margin_rate=Decimal("0.40"), big_promo=Decimal("4000.00")))
    db.flush()


def test_product_sheet_sku_expanded_with_formulas(db_session):
    _seed(db_session)
    wb = dx.build_full_export_workbook(db_session)

    # 产品总表置顶, 定价总表次之
    assert wb.sheetnames[0] == "产品总表"
    assert "定价总表" in wb.sheetnames[1]
    ws = wb["产品总表"]

    # 表头中文 + 关键列位置
    assert ws.cell(1, 10).value == "SKU编码"
    assert ws.cell(1, 20).value == "大促价"
    assert ws.cell(1, 23).value == "毛利率验算(公式)"

    # P100 两个 SKU 各一行 + P200 无SKU 一行
    sku_rows = {ws.cell(r, 10).value for r in range(2, ws.max_row + 1)}
    assert {"P100-A", "P100-B"}.issubset(sku_rows)
    assert None in sku_rows   # P200 无 SKU 保留一行(SKU编码空)

    # 大促价(T列) = VLOOKUP 关联定价总表 by sku_code($J)
    t2 = ws.cell(2, 20).value
    assert isinstance(t2, str) and t2.startswith("=IFERROR(VLOOKUP($J2,")
    assert "定价总表" in t2
    # 毛利率验算 = 1 - 会计成本(M)/大促价(T)
    assert ws.cell(2, 23).value.startswith("=IFERROR(1-M2/T2")
    # 数字格式
    assert ws.cell(2, 20).number_format == "#,##0.00"
    assert ws.cell(2, 14).number_format == "0.00%"     # 毛利率
    # 枚举值转中文: 重要程度 high→高 (col 7)
    assert ws.cell(2, 7).value == "高"

    # 冻结 + 自动筛选
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref is not None


def test_decimal_written_as_number_not_text(db_session):
    _seed(db_session)
    wb = dx.build_full_export_workbook(db_session)
    pname = wb.sheetnames[1]
    pws = wb[pname]
    # 定价总表里至少有一个会计成本/价格单元格是数字(float), 不是字符串
    numeric = [pws.cell(r, c).value
               for r in range(2, pws.max_row + 1)
               for c in range(7, 16)
               if isinstance(pws.cell(r, c).value, (int, float))]
    assert numeric, "Decimal 应写成数字而非文本"


def test_full_export_runs_over_all_entities(db_session):
    """空库也要能跑完全部28实体不崩(每类目一空 Sheet)。"""
    wb = dx.build_full_export_workbook(db_session)
    assert "产品总表" in wb.sheetnames
    assert len(wb.sheetnames) >= 20


def test_cell_strips_tzinfo():
    """TimestampMixin 是 tz-aware datetime, openpyxl 不能写带时区时间 → _cell 必须剥 tzinfo。"""
    from datetime import datetime, timezone
    from app.services.exceptions_export_service import _cell
    out = _cell(datetime(2026, 6, 17, 8, 0, tzinfo=timezone.utc))
    assert isinstance(out, datetime) and out.tzinfo is None


def test_full_export_saves_with_real_timestamps(db_session):
    """带时间戳的行能正常 save 成 xlsx 字节(回归: tz-aware datetime 曾导致 save 崩)。"""
    import io
    _seed(db_session)
    wb = dx.build_full_export_workbook(db_session)
    buf = io.BytesIO()
    wb.save(buf)        # 不抛 TypeError 即通过
    assert buf.getvalue()[:2] == b"PK"   # xlsx = zip

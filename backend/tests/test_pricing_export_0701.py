"""定价总表导出优化 (2026-07-01): 补全淘宝/小红书平台活动价 + 中文表头 (英文字段汉化)。"""
from decimal import Decimal

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services.data_export_service import build_full_export_workbook


def _pricing_ws(wb):
    for name in wb.sheetnames:
        if name.startswith("定价总表"):
            return wb[name]
    return None


def test_pricing_sheet_headers_cn_and_promo_columns(db_session):
    db_session.add(PricingSku(
        product_code="P1", sku_code="P1-A", product_name="测试岩板餐桌",
        big_promo=Decimal("1000"), factory_cost=Decimal("300"),
    ))
    db_session.add(PricingSkuPromo(
        sku_code="P1-A", taobao_activity_price=Decimal("800"),
        xhs_list_price=Decimal("900"), xhs_promo_price=Decimal("765"),
    ))
    db_session.commit()

    ws = _pricing_ws(build_full_export_workbook(db_session))
    assert ws is not None
    headers = [c.value for c in ws[1]]

    # 汉化: 原来的英文列名不再出现
    for eng in ("factory_cost_override", "base_list", "taobao_title",
                "physical_cost", "xhs_list_price", "taobao_activity_price"):
        assert eng not in headers, f"英文表头未汉化: {eng}"
    # 中文表头到位 (含补全的淘宝/小红书价格)
    for cn in ("工厂成本手动覆盖", "标价基数", "淘宝标题", "物理总成本",
               "淘宝活动价", "小红书标价", "小红书促销价"):
        assert cn in headers, f"缺中文表头: {cn}"


def test_pricing_sheet_promo_values_written(db_session):
    db_session.add(PricingSku(product_code="P1", sku_code="P1-A", product_name="测试桌"))
    db_session.add(PricingSkuPromo(sku_code="P1-A", xhs_list_price=Decimal("900")))
    db_session.commit()

    ws = _pricing_ws(build_full_export_workbook(db_session))
    headers = [c.value for c in ws[1]]
    col = headers.index("小红书标价") + 1
    assert float(ws.cell(2, col).value) == 900.0     # 补全的小红书价确实写进了数据行

"""定价图册 (带图导出, 2026-07-01): 一产品一行 + 售价; 图库文件在测试里 stub 掉。"""
from decimal import Decimal

import app.services.pricing_catalog_service as pcs
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services.pricing_catalog_service import build_catalog_html


def test_catalog_has_product_and_prices(db_session, monkeypatch):
    monkeypatch.setattr(pcs, "_img_data_uri", lambda url: None)   # 不碰真实图库文件
    db_session.add(Product(code="P1", name="测试岩板餐桌"))
    db_session.add(PricingSku(product_code="P1", sku_code="P1-A", sku="1.2米",
                              list_price=Decimal("6880"), big_promo=Decimal("3060")))
    db_session.commit()
    out = build_catalog_html(db_session)
    assert "定价图册" in out
    assert "测试岩板餐桌" in out and "P1" in out
    assert "¥3,060" in out and "¥6,880" in out
    assert "暂无图片" in out                    # 无图 → 占位框


def test_catalog_limit_and_skips_unpriced(db_session, monkeypatch):
    monkeypatch.setattr(pcs, "_img_data_uri", lambda url: None)
    for i in range(4):
        db_session.add(Product(code=f"P{i}", name=f"产品{i}"))
        db_session.add(PricingSku(product_code=f"P{i}", sku_code=f"P{i}-A", big_promo=Decimal("100")))
    db_session.add(Product(code="NOPRICE", name="无定价产品"))   # 无 SKU → 不进图册
    db_session.commit()
    assert build_catalog_html(db_session).count('class="card"') == 4      # NOPRICE 被排除
    assert build_catalog_html(db_session, limit=2).count('class="card"') == 2


def test_catalog_skips_non_product_skus(db_session, monkeypatch):
    monkeypatch.setattr(pcs, "_img_data_uri", lambda url: None)
    db_session.add(Product(code="REAL1", name="孚格蜂蜜餐桌"))
    db_session.add(PricingSku(product_code="REAL1", sku_code="REAL1-A", big_promo=Decimal("1830")))
    for code, nm in [("V1", "作废洞石大柜链接"), ("S1", "商家安装sku"),
                     ("C1", "全屋定制"), ("PPS99999999999", "纯定制")]:
        db_session.add(Product(code=code, name=nm))
        db_session.add(PricingSku(product_code=code, sku_code=f"{code}-A", big_promo=Decimal("1")))
    db_session.commit()
    out = build_catalog_html(db_session)
    assert out.count('class="card"') == 1        # 只剩真实产品
    assert "孚格蜂蜜餐桌" in out
    for junk in ("作废洞石大柜链接", "商家安装sku", "全屋定制", "纯定制"):
        assert junk not in out


def test_catalog_xlsx_structure(db_session, monkeypatch):
    """带图 Excel: 中文表头 + 分类色带 + 同编码多 SKU 图列合并; 无图退占位; 作废剔除。"""
    import io
    import openpyxl
    from app.services.data_export_service import build_catalog_xlsx
    monkeypatch.setattr(pcs, "product_image_map", lambda codes, url_by_code, **k: {})  # 不碰真实图
    db_session.add(Product(code="P1", name="测试岩板餐桌"))
    db_session.add(PricingSku(product_code="P1", sku_code="P1-A", sku="1.2米",
                              list_price=Decimal("6880"), big_promo=Decimal("3060")))
    db_session.add(PricingSku(product_code="P1", sku_code="P1-B", sku="1.4米",
                              list_price=Decimal("7880"), big_promo=Decimal("3560")))
    db_session.add(Product(code="V1", name="作废旧链接"))            # 应被剔除
    db_session.add(PricingSku(product_code="V1", sku_code="V1-A", big_promo=Decimal("1")))
    db_session.commit()

    wb = openpyxl.load_workbook(io.BytesIO(build_catalog_xlsx(db_session).getvalue()))
    ws = wb["定价图册"]
    assert ws.cell(1, 1).value == "产品图"                          # 图列表头
    row1 = [c.value for c in ws[1] if c.value]
    assert "售价档位" in row1 and "标识" in row1                     # 分类色带
    hdr = {c.value: c.column for c in ws[2] if c.value}
    for h in ("产品编码", "产品名称", "标价", "大促价", "淘宝标题", "小红书标价"):
        assert h in hdr
    pc = hdr["产品编码"]
    codes = [ws.cell(r, pc).value for r in range(3, ws.max_row + 1)]
    assert codes == ["P1", "P1"]                                    # 作废剔除, P1 两个SKU
    assert "A3:A4" in {str(m) for m in ws.merged_cells.ranges}      # 图列纵向合并
    assert ws.cell(3, 1).value == "暂无图片"                         # 无图占位
    vals = {ws.cell(r, c).value for r in range(3, ws.max_row + 1) for c in range(1, ws.max_column + 1)}
    assert 3060 in vals and 6880 in vals                            # 售价落格

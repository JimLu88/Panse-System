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

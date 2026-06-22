# -*- coding: utf-8 -*-
"""客服集成接口 /api/cs/products/{code} 回归: 不应因访问不存在字段而 500 (2026-06-22)。"""
from app.api.cs_integration import cs_product_detail
from app.models.product import Product


def test_cs_product_detail_serializes_ok(db_session):
    db_session.add(Product(code="PPSTEST001", name="测试餐桌", image_url="x.jpg"))
    db_session.flush()
    r = cs_product_detail(code="PPSTEST001", _=True, db=db_session)
    assert r["code"] == "PPSTEST001"
    assert r["image_url"] == "x.jpg"
    assert "gallery" in r                     # 图库由 gallery 字段提供
    assert "gallery_image_url" not in r       # 已移除的无效字段不再出现
    assert isinstance(r["skus"], list)

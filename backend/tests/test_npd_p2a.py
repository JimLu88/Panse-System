"""NPD P2a: 设计落地自动建档 — 新配件自动建 Material + Product + BomLine + draft PricingSku。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.material import Material
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import npd_service


def test_materialize_builds_full_archive(db_session):
    npd_service.seed_stages(db_session)
    p = npd_service.create_project(db_session, name="岩板餐桌", target_price=Decimal("2000"))
    npd_service.add_bom_line(db_session, p.id, material_name="12mm岩板台面", category="岩板",
                             unit="片", qty=Decimal("1"), unit_price=Decimal("500"), is_new=True)
    npd_service.add_bom_line(db_session, p.id, material_name="餐桌钢架", category="五金",
                             unit="套", qty=Decimal("1"), unit_price=Decimal("300"), is_new=True)
    res = npd_service.materialize_project(db_session, p, brand="BS", category_code="01")
    pcode = res["product_code"]
    assert res["materials_created"] == 2
    assert db_session.query(Product).filter_by(code=pcode).count() == 1
    assert db_session.query(BomLine).filter_by(product_code=pcode).count() == 2
    sku = db_session.query(PricingSku).filter_by(product_code=pcode).one()
    assert sku.physical_cost == Decimal("800")     # 500 + 300
    assert db_session.query(Material).filter_by(name="12mm岩板台面").count() == 1
    assert p.product_code == pcode
    # 幂等保护: 重复生成被拒
    with pytest.raises(ValueError):
        npd_service.materialize_project(db_session, p, brand="BS", category_code="01")


def test_materialize_reuses_existing_material_by_name(db_session):
    npd_service.seed_stages(db_session)
    db_session.add(Material(code="AC-9001", name="现有五金", price=Decimal("50"), category="五金"))
    db_session.commit()
    p = npd_service.create_project(db_session, name="复用单", target_price=Decimal("1000"))
    npd_service.add_bom_line(db_session, p.id, material_name="现有五金", qty=Decimal("2"), is_new=True)
    res = npd_service.materialize_project(db_session, p, brand="BS", category_code="02")
    assert res["materials_created"] == 0           # 同名复用, 不重复建
    sku = db_session.query(PricingSku).filter_by(product_code=res["product_code"]).one()
    assert sku.physical_cost == Decimal("100")     # 50 × 2


def test_materialize_requires_bom_and_valid_codes(db_session):
    npd_service.seed_stages(db_session)
    p = npd_service.create_project(db_session, name="空BOM", target_price=Decimal("1000"))
    with pytest.raises(ValueError):                # 无 BOM
        npd_service.materialize_project(db_session, p, brand="BS", category_code="01")
    npd_service.add_bom_line(db_session, p.id, material_name="某件", qty=Decimal("1"), is_new=True)
    with pytest.raises(ValueError):                # 品牌码非法
        npd_service.materialize_project(db_session, p, brand="BSX", category_code="01")
    with pytest.raises(ValueError):                # 类目码非法
        npd_service.materialize_project(db_session, p, brand="BS", category_code="餐桌")

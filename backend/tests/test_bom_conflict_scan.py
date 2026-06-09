"""BOM / 物料 一致性冲突扫描 → 异常 (Block C):
一个 sku_code 挂多产品 / 料号名冲突 / 占位未填。"""
from decimal import Decimal

from app.models.bom import BomLine
from app.models.exception import DataException
from app.models.material import Material
from app.services import data_quality_service as dq


def _open(db, etype):
    return db.query(DataException).filter_by(exception_type=etype, status="open").count()


def test_scan_bom_product_collision(db_session):
    db = db_session
    db.add_all([
        BomLine(product_code="PA", product_name="孚格餐桌", sku_code="SDUP",
                material_code="AC-1", qty_per_product=Decimal("1")),
        BomLine(product_code="PB", product_name="岩板餐桌", sku_code="SDUP",
                material_code="AC-2", qty_per_product=Decimal("1")),
        BomLine(product_code="PC", product_name="单独产品", sku_code="SOK",
                material_code="AC-3", qty_per_product=Decimal("1")),
    ])
    db.commit()
    n = dq.scan_bom_product_collision(db)
    db.commit()
    assert n == 1                                  # 只有 SDUP 一码挂两产品
    exc = db.query(DataException).filter_by(exception_type="bom_product_collision", status="open").one()
    assert exc.source_pk == "SDUP" and "孚格餐桌" in exc.description
    assert dq.scan_bom_product_collision(db) == 1  # 再扫仍报这1个问题
    assert _open(db, "bom_product_collision") == 1  # 但不重复建异常(幂等)


def test_scan_material_name_conflict(db_session):
    db = db_session
    db.add_all([Material(code="AC-0074", name="5mm超白玻璃"), Material(code="AC-OK", name="一致名")])
    db.add_all([
        BomLine(product_code="P", sku_code="S", material_code="AC-0074",
                material_name="榉木餐桌金属固定杆", qty_per_product=Decimal("1")),
        BomLine(product_code="P", sku_code="S", material_code="AC-OK",
                material_name="一致名", qty_per_product=Decimal("1")),   # 一致 → 不报
    ])
    db.commit()
    n = dq.scan_material_name_conflict(db)
    db.commit()
    assert n == 1
    exc = db.query(DataException).filter_by(exception_type="material_name_conflict", status="open").one()
    assert exc.source_pk == "AC-0074"
    assert "5mm超白玻璃" in exc.description and "金属固定杆" in exc.description


def test_scan_material_placeholder_only_if_used(db_session):
    db = db_session
    db.add_all([
        Material(code="WD-1", name="占位 (WD-1)"),   # 被 BOM 引用 → 报
        Material(code="WD-2", name="占位 (WD-2)"),   # 没被引用 → 不打扰
    ])
    db.add(BomLine(product_code="P", sku_code="S", material_code="WD-1",
                   material_name="榉木腿", qty_per_product=Decimal("1")))
    db.commit()
    n = dq.scan_material_placeholder(db)
    db.commit()
    assert n == 1
    exc = db.query(DataException).filter_by(exception_type="material_placeholder", status="open").one()
    assert exc.source_pk == "WD-1" and "榉木腿" in (exc.suggestion_action or "")

"""删产品 / 删 BOM 行 (Block D): 防误删(被订单引用拦截) + 级联删 BOM。"""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.bom import delete_bom_line
from app.api.products import delete_product
from app.models.bom import BomLine
from app.models.order import Order
from app.models.product import Product


def test_delete_bom_line(db_session):
    db = db_session
    line = BomLine(product_code="P", sku_code="S", material_code="AC-1", qty_per_product=Decimal("1"))
    db.add(line)
    db.commit()
    delete_bom_line(line.id, db)
    assert db.query(BomLine).count() == 0


def test_delete_bom_line_404(db_session):
    with pytest.raises(HTTPException) as e:
        delete_bom_line(999999, db_session)
    assert e.value.status_code == 404


def test_delete_product_blocked_by_orders_then_force_cascades(db_session):
    db = db_session
    prod = Product(code="PDEL", name="重复产品")
    db.add(prod)
    db.add_all([
        BomLine(product_code="PDEL", sku_code="S1", material_code="AC-1", qty_per_product=Decimal("1")),
        BomLine(product_code="PDEL", sku_code="S1", material_code="AC-2", qty_per_product=Decimal("1")),
        Order(platform="淘宝", order_no="ODEL", product_code="PDEL", qty=1, status="paid"),
    ])
    db.commit()
    # 被订单引用 → 拦截 (force=False)
    with pytest.raises(HTTPException) as e:
        delete_product(prod.id, force=False, db=db)
    assert e.value.status_code == 409
    assert db.query(Product).filter_by(code="PDEL").count() == 1   # 没被删

    # force=True → 删产品 + 级联删 BOM
    r = delete_product(prod.id, force=True, db=db)
    assert r["deleted_product"] == "PDEL" and r["deleted_bom_lines"] == 2
    assert db.query(Product).filter_by(code="PDEL").count() == 0
    assert db.query(BomLine).filter_by(product_code="PDEL").count() == 0


def test_delete_product_no_orders_deletes_directly(db_session):
    db = db_session
    prod = Product(code="PFREE", name="无订单产品")
    db.add(prod)
    db.add(BomLine(product_code="PFREE", sku_code="S2", material_code="AC-9", qty_per_product=Decimal("1")))
    db.commit()
    r = delete_product(prod.id, force=False, db=db)   # 无订单引用 → 直接删
    assert r["deleted_product"] == "PFREE" and r["deleted_bom_lines"] == 1
    assert db.query(Product).filter_by(code="PFREE").count() == 0

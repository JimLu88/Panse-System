from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.custom_variant import CustomVariant
from app.models.material import Material
from app.services import customization_service


def _setup_bom(db):
    db.add(Material(code="WD-001", name="木作"))
    db.add(Material(code="AC-001", name="床铺板"))
    db.add(Material(code="AC-009", name="金属腿"))
    db.add_all([
        BomLine(
            product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100100118",
            material_code="WD-001", unit="套", qty_per_product=Decimal("1"),
            size_type="组合",
        ),
        BomLine(
            product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100100118",
            material_code="AC-001", unit="套", qty_per_product=Decimal("1"),
            size_type="组合",
        ),
        BomLine(
            product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100100118",
            material_code="AC-009", unit="个", qty_per_product=Decimal("4"),
            size_type="个数",
        ),
    ])
    db.flush()


def test_preview_returns_diff(db_session):
    _setup_bom(db_session)
    r = customization_service.preview(
        db_session,
        base_sku_code="PPS00100100118",
        dimension_changes={"宽": 1900, "长": 2100},
    )
    assert r.base_sku_code == "PPS00100100118"
    assert r.proposed_custom_sku_code == "PPS00100100118改01"
    assert len(r.diff_lines) == 3
    combo_lines = [d for d in r.diff_lines if "按定制尺寸" in (d.note or "")]
    assert len(combo_lines) == 2  # WD/AC 组合件都标了 note
    assert all("宽=1900" in (d.note or "") for d in combo_lines)


def test_preview_empty_dimension_rejected(db_session):
    _setup_bom(db_session)
    with pytest.raises(ValueError):
        customization_service.preview(
            db_session, base_sku_code="PPS00100100118", dimension_changes={}
        )


def test_preview_unknown_sku_rejected(db_session):
    with pytest.raises(ValueError):
        customization_service.preview(
            db_session, base_sku_code="NOPE", dimension_changes={"x": 1}
        )


def test_confirm_creates_variant_and_clones_bom(db_session):
    _setup_bom(db_session)
    r = customization_service.confirm(
        db_session,
        base_sku_code="PPS00100100118",
        dimension_changes={"长": 2100},
        order_no="O123",
    )
    assert r.custom_sku_code == "PPS00100100118改01"
    assert r.cloned_bom_lines == 3
    # 新 BOM 行存在
    new_bom = db_session.query(BomLine).filter_by(sku_code="PPS00100100118改01").all()
    assert len(new_bom) == 3
    # CustomVariant 留痕
    cv = db_session.query(CustomVariant).filter_by(custom_sku_code="PPS00100100118改01").one()
    assert cv.base_sku_code == "PPS00100100118"
    assert cv.related_order_no == "O123"
    assert cv.dimension_overrides == {"长": 2100}


def test_confirm_increments_serial_for_repeated_customization(db_session):
    _setup_bom(db_session)
    r1 = customization_service.confirm(
        db_session, base_sku_code="PPS00100100118", dimension_changes={"长": 2100},
    )
    r2 = customization_service.confirm(
        db_session, base_sku_code="PPS00100100118", dimension_changes={"长": 2200},
    )
    assert r1.custom_sku_code == "PPS00100100118改01"
    assert r2.custom_sku_code == "PPS00100100118改02"


def test_confirm_with_qty_overrides(db_session):
    _setup_bom(db_session)
    r = customization_service.confirm(
        db_session,
        base_sku_code="PPS00100100118",
        dimension_changes={"长": 2200},
        qty_overrides={"AC-009": Decimal("6")},  # 加长床需要 6 条腿
    )
    new_bom = db_session.query(BomLine).filter_by(sku_code="PPS00100100118改01").all()
    leg = next(b for b in new_bom if b.material_code == "AC-009")
    assert leg.qty_per_product == Decimal("6")

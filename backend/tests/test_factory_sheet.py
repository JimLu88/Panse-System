from datetime import date
from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.custom_variant import CustomVariant
from app.models.material import Material
from app.models.order import Order
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import factory_sheet


def _setup(db):
    db.add(Product(code="PPS001", name="榉木无边床"))
    db.add(PricingSku(
        product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100118",
        size_category="大型", image_url="https://x/p.png",
    ))
    db.add(Material(code="WD-001", name="木作部分", unit="套"))
    db.add(Material(code="AC-009", name="金属腿", unit="个"))
    db.add(BomLine(
        product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100118",
        material_code="WD-001", unit="套", qty_per_product=Decimal("1"),
    ))
    db.add(BomLine(
        product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100118",
        material_code="AC-009", unit="个", qty_per_product=Decimal("4"),
    ))
    db.flush()


def test_basic_factory_sheet(db_session):
    _setup(db_session)
    o = Order(
        platform="淘宝", order_no="X1",
        order_date=date(2026, 5, 7), ship_date=date(2026, 5, 25),
        customer_name="钱丹妮",
        customer_phone="18466944201-5274",
        customer_address="上海市青浦区徐泾镇绿城春晓园28-702",
        product_code="PPS001", product_name="榉木无边床",
        sku="榉木无边床-1.8米", sku_code="PPS00100118",
        qty=1,
    )
    db_session.add(o)
    db_session.flush()
    sheet = factory_sheet.build(db_session, o.id)

    assert sheet.order_no == "X1"
    assert sheet.product_name == "榉木无边床"
    assert sheet.customer_name == "钱丹妮"
    assert sheet.image_url == "https://x/p.png"
    assert len(sheet.materials) == 2
    # qty 系数累加: 总件数 = 4 个腿 × 1 套 = 4
    legs = next(m for m in sheet.materials if m.material_code == "AC-009")
    assert legs.total_qty == Decimal("4")
    assert sheet.warnings == []  # 干净


def test_factory_sheet_qty_multiplies_total(db_session):
    _setup(db_session)
    o = Order(
        platform="淘宝", order_no="X2",
        order_date=date(2026, 5, 7),
        product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100118",
        qty=3,
    )
    db_session.add(o)
    db_session.flush()
    sheet = factory_sheet.build(db_session, o.id)
    legs = next(m for m in sheet.materials if m.material_code == "AC-009")
    assert legs.total_qty == Decimal("12")  # 4 × 3


def test_encrypted_address_creates_warning(db_session):
    _setup(db_session)
    o = Order(
        platform="淘宝", order_no="X3",
        order_date=date(2026, 5, 18),
        customer_address="浙江省杭州市萧山区**********",
        product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100118",
        qty=1,
    )
    db_session.add(o)
    db_session.flush()
    sheet = factory_sheet.build(db_session, o.id)
    warnings = [w for w in sheet.warnings if w.code == "encrypted_address"]
    assert len(warnings) == 1
    assert warnings[0].severity == "error"


def test_unknown_product_warning(db_session):
    _setup(db_session)
    o = Order(
        platform="淘宝", order_no="X4", order_date=date(2026, 5, 18),
        product_code="GHOST_PROD", qty=1,
    )
    db_session.add(o)
    db_session.flush()
    sheet = factory_sheet.build(db_session, o.id)
    assert any(w.code == "unknown_product" for w in sheet.warnings)


def test_no_bom_warning(db_session):
    _setup(db_session)
    o = Order(
        platform="淘宝", order_no="X5", order_date=date(2026, 5, 18),
        product_code="PPS001", sku_code="UNBUILT_SKU", qty=1,
    )
    db_session.add(o)
    db_session.flush()
    sheet = factory_sheet.build(db_session, o.id)
    assert any(w.code == "no_bom" for w in sheet.warnings)


def test_custom_variant_flagged(db_session):
    _setup(db_session)
    db_session.add(CustomVariant(
        base_sku_code="PPS00100118",
        custom_sku_code="PPS00100118改01",
        product_code="PPS001",
        dimension_overrides={"长": 2100},
    ))
    # 为 改 SKU 也加一条 BOM 行避免 no_bom 警告
    db_session.add(BomLine(
        product_code="PPS001", sku="榉木无边床-1.8米", sku_code="PPS00100118改01",
        material_code="WD-001", qty_per_product=Decimal("1"),
    ))
    o = Order(
        platform="淘宝", order_no="X6", order_date=date(2026, 5, 18),
        product_code="PPS001", sku_code="PPS00100118改01", qty=1,
    )
    db_session.add(o)
    db_session.flush()
    sheet = factory_sheet.build(db_session, o.id)
    assert sheet.is_custom_variant is True
    assert sheet.dimension_changes == {"长": 2100}


def test_missing_order_raises(db_session):
    with pytest.raises(ValueError):
        factory_sheet.build(db_session, 9999)


def test_ambiguous_placeholder_sku_does_not_abort_sheet(db_session):
    db_session.add_all([
        PricingSku(product_code="P-A", sku_code="P-A-99", sku="材质定制咨询"),
        PricingSku(product_code="P-B", sku_code="P-B-99", sku="材质定制咨询"),
    ])
    order = Order(
        platform="淘宝",
        order_no="AMB-1",
        order_date=date(2026, 8, 7),
        product_name="客户实际购买的双人床",
        sku="材质定制咨询",
        qty=1,
    )
    db_session.add(order)
    db_session.flush()

    sheet = factory_sheet.build(db_session, order.id)

    assert sheet.product_name == "客户实际购买的双人床"
    assert sheet.product_code is None
    assert any(w.code == "ambiguous_sku_name" for w in sheet.warnings)


def test_sheet_title_format(db_session):
    _setup(db_session)
    o = Order(
        platform="淘宝", order_no="5112861625016010242",
        order_date=date(2026, 5, 7),
        product_code="PPS001", sku_code="PPS00100118", qty=1,
    )
    db_session.add(o)
    db_session.flush()
    sheet = factory_sheet.build(db_session, o.id)
    assert "5月7日" in sheet.sheet_title
    assert "0242" in sheet.sheet_title  # 末 4 位订单号


def test_render_shows_customer_note(db_session):
    """补拍链/定制单真实需求只在备注(buyer_message)里, 下单图必须渲染出来给工厂 (2026-07-09)。"""
    _setup(db_session)
    from app.services.order_sheet_archive_service import render_html
    o = Order(
        platform="淘宝", order_no="X7", order_date=date(2026, 7, 8),
        product_name="畔色木作 差价邮费补拍专链",
        buyer_message="定制1.8*0.65白色岩板餐桌", qty=1, factory_no=293,
    )
    db_session.add(o)
    db_session.flush()
    sheet = factory_sheet.build(db_session, o.id)
    assert sheet.remark == "定制1.8*0.65白色岩板餐桌"   # buyer_message → 客户备注
    html = render_html(sheet)
    assert "客户备注" in html                            # 有备注区
    assert "定制1.8*0.65白色岩板餐桌" in html            # 备注内容渲染出来, 工厂知道做什么

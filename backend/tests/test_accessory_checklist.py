"""订单配件清单服务: BOM 生成 + 客户备注新增配件 + 状态/物流字段。"""
from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order, OrderAccessoryItem
from app.services import accessory_checklist_service as svc


@pytest.fixture
def order_with_bom(db_session):
    db_session.add_all([
        Material(code="AC-0001", name="抽屉滑轨", unit="套", price=Decimal("12")),
        Material(code="MW-0001", name="实木板", unit="张", price=Decimal("80")),
        Material(code="抱枕", name="抱枕", unit="个", price=Decimal("30")),  # 名称匹配用
    ])
    db_session.add_all([
        BomLine(product_code="P1", sku_code="S1", material_code="AC-0001",
                qty_per_product=Decimal("2"), unit="套"),
        BomLine(product_code="P1", sku_code="S1", material_code="MW-0001",
                qty_per_product=Decimal("1"), unit="张"),
    ])
    order = Order(platform="淘宝", order_no="O1", sku_code="S1", qty=3,
                  status="paid")
    db_session.add(order)
    db_session.commit()
    return order


def test_generate_splits_factory_vs_purchase(db_session, order_with_bom):
    created = svc.generate_for_order(db_session, order_with_bom.id)
    by_code = {c.material_code: c for c in created}

    # AC-* 需采购
    ac = by_code["AC-0001"]
    assert ac.is_factory_provided is False
    assert ac.status == "未采购"
    assert ac.source == "bom"
    assert ac.qty_required == Decimal("6")  # 2/件 × 3件

    # MW-* 工厂提供
    mw = by_code["MW-0001"]
    assert mw.is_factory_provided is True
    assert mw.status == "工厂提供"


def test_generate_is_idempotent(db_session, order_with_bom):
    svc.generate_for_order(db_session, order_with_bom.id)
    again = svc.generate_for_order(db_session, order_with_bom.id)
    assert again == []  # 不重复创建
    assert len(svc.get_checklist(db_session, order_with_bom.id)) == 2


def test_extra_accessories_from_remark(db_session, order_with_bom):
    svc.generate_for_order(db_session, order_with_bom.id)
    extra = svc.add_extra_accessories(
        db_session, order_with_bom.id,
        [{"name": "抱枕", "qty": 2, "note": "客户要加2个抱枕"},
         {"name": "未知小物", "qty": 1, "note": "送的"}],
    )
    by_code = {e.material_code: e for e in extra}
    # 名称匹配到 Material → 用真实编码; 整单绝对数量, 不乘件数
    assert "抱枕" in by_code
    assert by_code["抱枕"].qty_required == Decimal("2")
    assert by_code["抱枕"].source == "客户备注"
    # 匹配不到 → 占位编码 NEW-
    assert any(c.startswith("NEW-") for c in by_code)


def test_extra_accessories_flow_into_factory_sheet(db_session, order_with_bom):
    from app.services import factory_sheet
    svc.generate_for_order(db_session, order_with_bom.id)
    svc.add_extra_accessories(
        db_session, order_with_bom.id,
        [{"name": "抱枕", "qty": 2, "note": "加2个抱枕"}],
    )
    sheet = factory_sheet.build(db_session, order_with_bom.id)
    extra_rows = [m for m in sheet.materials if m.source == "客户备注"]
    assert len(extra_rows) == 1
    assert extra_rows[0].material_name == "抱枕"
    assert extra_rows[0].total_qty == Decimal("2")
    # 应有 extra_accessory 警告
    assert any(w.code == "extra_accessory" for w in sheet.warnings)

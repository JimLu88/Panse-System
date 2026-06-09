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


# ---- 一个 sku_code 在 BOM 里挂了多个产品时, 按 product_code 消歧, 不串料 ----
def test_summary_by_order(db_session, order_with_bom):
    svc.generate_for_order(db_session, order_with_bom.id)
    s = svc.summary_by_order(db_session)
    entry = s[order_with_bom.id]
    assert entry["total"] == 2          # AC-0001 + MW-0001
    assert entry["done"] == 1           # MW-0001 工厂提供 → 算配齐
    assert entry["pending"] == 1        # AC-0001 未采购 → 还缺


def test_mark_all_arrived_clears_pending(db_session):
    db = db_session
    order = Order(platform="淘宝", order_no="OM", product_code="P", sku_code="S", qty=1, status="paid")
    db.add(order)
    db.commit()
    db.add_all([
        OrderAccessoryItem(order_id=order.id, order_no="OM", material_code="AC-1", material_name="玻璃",
                           qty_required=Decimal("1"), source="bom", status="未采购"),
        OrderAccessoryItem(order_id=order.id, order_no="OM", material_code="AC-2", material_name="五金",
                           qty_required=Decimal("1"), source="bom", status="已下单"),
    ])
    db.commit()
    assert svc.mark_all_arrived(db, order.id) == 2
    assert all(i.status == "已到货" for i in svc.get_checklist(db, order.id))


def test_woodwork_defaults_done_and_named(db_session):
    db = db_session
    db.add(Material(code="WD-0036", name="占位 (WD-0036)", unit="套"))   # 木作料号, 物料库占位
    db.add(BomLine(product_code="PW", sku_code="SW", material_code="WD-0036", qty_per_product=Decimal("2")))
    order = Order(platform="淘宝", order_no="OW", product_code="PW", sku_code="SW", qty=1, status="paid")
    db.add(order)
    db.commit()
    it = svc.generate_for_order(db, order.id)[0]
    assert it.material_name == "木作部分"      # 占位 → 木作部分(全名)
    assert it.status == "已到货"               # 木作默认已备, 不当外购缺料
    assert it.is_factory_provided is False


def test_resync_bumps_untouched_woodwork_to_done(db_session):
    db = db_session
    db.add(Material(code="WD-1", name="占位 (WD-1)", unit="套"))
    db.add(BomLine(product_code="PW", sku_code="SW", material_code="WD-1", qty_per_product=Decimal("1")))
    order = Order(platform="淘宝", order_no="OW2", product_code="PW", sku_code="SW", qty=1, status="paid")
    db.add(order)
    db.commit()
    # 历史行: 木作还停在旧默认"未采购"
    db.add(OrderAccessoryItem(order_id=order.id, order_no="OW2", material_code="WD-1",
                              material_name="占位 (WD-1)", qty_required=Decimal("1"), source="bom", status="未采购"))
    db.commit()
    items = svc.resync_for_order(db, order.id)
    assert items[0].status == "已到货" and items[0].material_name == "木作部分"


def test_by_component_aggregates_across_orders(db_session):
    db = db_session
    db.add_all([
        OrderAccessoryItem(order_id=1, order_no="O1", material_code="AC-RAIL", material_name="电力轨道1米",
                           qty_required=Decimal("2"), unit="根", source="bom", status="未采购", is_factory_provided=False),
        OrderAccessoryItem(order_id=2, order_no="O2", material_code="AC-RAIL", material_name="电力轨道1米",
                           qty_required=Decimal("3"), unit="根", source="bom", status="已下单", is_factory_provided=False),
        OrderAccessoryItem(order_id=3, order_no="O3", material_code="MW-X", material_name="木作",
                           qty_required=Decimal("1"), source="bom", status="工厂提供", is_factory_provided=True),
        OrderAccessoryItem(order_id=4, order_no="O4", material_code="AC-RAIL", material_name="电力轨道1米",
                           qty_required=Decimal("5"), unit="根", source="bom", status="已到货", is_factory_provided=False),
    ])
    db.commit()
    out = svc.by_component(db)
    rail = next(g for g in out if g["material_code"] == "AC-RAIL")
    assert rail["to_buy_qty"] == "2"            # O1 未采购
    assert rail["bought_pending_qty"] == "3"    # O2 已下单(已购买未到)
    assert rail["order_count"] == 2             # O1+O2 (O4已到货不计入待办)
    assert all(g["material_code"] != "MW-X" for g in out)   # 工厂提供不进采购视图


def test_bulk_update_bought_and_self_delivered(db_session):
    db = db_session
    a = OrderAccessoryItem(order_id=1, order_no="O1", material_code="AC-G", material_name="玻璃",
                           qty_required=Decimal("1"), source="bom", status="未采购", is_factory_provided=False)
    b = OrderAccessoryItem(order_id=2, order_no="O2", material_code="AC-G", material_name="玻璃",
                           qty_required=Decimal("1"), source="bom", status="未采购", is_factory_provided=False,
                           tracking_no="SF123")
    db.add_all([a, b])
    db.commit()
    assert svc.bulk_update(db, [a.id], status="已下单", purchase_no="PO-2026-01") == 1
    db.refresh(a)
    assert a.status == "已下单" and a.purchase_no == "PO-2026-01"   # 已购买 + 采购单号
    svc.bulk_update(db, [b.id], status="已到货", self_delivered=True)   # 自送 → 已到货 + 清物流号
    db.refresh(b)
    assert b.self_delivered is True and b.status == "已到货" and b.tracking_no is None


def test_generate_disambiguates_by_product_code(db_session):
    db = db_session
    db.add_all([
        Material(code="AC-T", name="桌专用料", unit="块"),
        Material(code="AC-G", name="玻璃(别的产品的)", unit="根"),
    ])
    # 同一 sku_code 'SX' 在 BOM 里挂了两个产品(脏数据)
    db.add_all([
        BomLine(product_code="P_TABLE", sku_code="SX", material_code="AC-T", qty_per_product=Decimal("1")),
        BomLine(product_code="P_OTHER", sku_code="SX", material_code="AC-G", qty_per_product=Decimal("2")),
    ])
    order = Order(platform="淘宝", order_no="OD1", product_code="P_TABLE", sku_code="SX", qty=1, status="paid")
    db.add(order)
    db.commit()
    created = svc.generate_for_order(db, order.id)
    assert {c.material_code for c in created} == {"AC-T"}   # 只本产品的料, 不串入 P_OTHER 的玻璃


def test_generate_uses_bom_name_when_material_is_placeholder(db_session):
    db = db_session
    db.add(Material(code="WD-9", name="占位 (WD-9)", unit="套"))   # 物料库还是占位
    db.add(BomLine(product_code="PZ", sku_code="SZ", material_code="WD-9",
                   material_name="榉木腿", qty_per_product=Decimal("1")))
    order = Order(platform="淘宝", order_no="OD3", product_code="PZ", sku_code="SZ", qty=1, status="paid")
    db.add(order)
    db.commit()
    created = svc.generate_for_order(db, order.id)
    assert created[0].material_name == "榉木腿"   # 退回 BOM 名, 不显示"占位"


def test_resync_removes_blended_and_refreshes_names(db_session):
    db = db_session
    db.add_all([
        Material(code="AC-T", name="桌专用料", unit="块"),
        Material(code="WD-1", name="占位 (WD-1)", unit="套"),
        Material(code="AC-G", name="玻璃", unit="根"),
    ])
    db.add_all([
        BomLine(product_code="P_TABLE", sku_code="SX", material_code="AC-T",
                material_name="桌专用料", qty_per_product=Decimal("1")),
        BomLine(product_code="P_TABLE", sku_code="SX", material_code="WD-1",
                material_name="榉木餐桌木作", qty_per_product=Decimal("1")),
    ])
    order = Order(platform="淘宝", order_no="OD2", product_code="P_TABLE", sku_code="SX", qty=1, status="paid")
    db.add(order)
    db.commit()
    # 历史脏数据: 串入了 AC-G(不属本产品), WD-1 名是占位; 用户已把 AC-T 标"已下单"(进度要保留)
    db.add_all([
        OrderAccessoryItem(order_id=order.id, order_no="OD2", material_code="AC-T",
                           material_name="桌专用料", qty_required=Decimal("1"), source="bom", status="已下单"),
        OrderAccessoryItem(order_id=order.id, order_no="OD2", material_code="WD-1",
                           material_name="占位 (WD-1)", qty_required=Decimal("1"), source="bom", status="未采购"),
        OrderAccessoryItem(order_id=order.id, order_no="OD2", material_code="AC-G",
                           material_name="玻璃", qty_required=Decimal("2"), source="bom", status="未采购"),
    ])
    db.commit()
    items = svc.resync_for_order(db, order.id)
    by = {i.material_code: i for i in items}
    assert set(by) == {"AC-T", "WD-1"}                  # 串料 AC-G 已删
    assert by["AC-T"].status == "已下单"                 # 已对上的料保留用户进度
    assert by["WD-1"].material_name == "榉木餐桌木作"     # 占位名按 BOM 刷新

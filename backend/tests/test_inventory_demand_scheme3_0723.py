from datetime import date, timedelta
from decimal import Decimal

from app.models.inventory import ProductInventory
from app.models.order import Order
from app.services import (
    inventory_demand_service as demand,
    inventory_monthly_report_service as monthly,
    inventory_restock_service as restock,
    product_inventory_service,
    sales_analytics,
    settings_service,
)


AS_OF = date(2026, 7, 23)


def _order(db, no, *, qty=1, sku_code="PPS2601001010111", sku="标准款",
           product_code="PPS26010010101", name="榉木餐桌", day=AS_OF,
           custom=False):
    row = Order(
        platform="淘宝",
        order_no=no,
        order_date=day,
        product_code=product_code,
        product_name=name,
        sku_code=sku_code,
        sku=sku,
        qty=qty,
        paid_amount=Decimal("100"),
        status="shipped",
        is_custom=custom,
        is_refill=False,
    )
    db.add(row)
    db.flush()
    return row


def test_quantity_cleaning_and_custom_detection(db_session):
    db = db_session
    _order(db, "NORMAL3", qty=3)
    _order(db, "COLLAPSE4", qty=4)
    _order(db, "WARN5", qty=5)
    _order(db, "BAD3200", qty=3200)
    _order(db, "SUFFIX99", qty=2, sku_code="PPS2601001010199")
    rows = {
        row.order_no: row
        for row in demand.load_observations(
            db, start=AS_OF - timedelta(days=1), end=AS_OF
        )
    }
    assert (
        rows["NORMAL3"].kind,
        rows["NORMAL3"].effective_qty,
        rows["NORMAL3"].anomaly,
    ) == ("standard", 3, None)
    assert (
        rows["COLLAPSE4"].kind,
        rows["COLLAPSE4"].effective_qty,
        rows["COLLAPSE4"].anomaly,
    ) == ("custom", 1, "qty_gt3")
    assert (rows["WARN5"].kind, rows["WARN5"].effective_qty, rows["WARN5"].anomaly) == (
        "custom", 1, "qty_gt3"
    )
    assert (rows["BAD3200"].kind, rows["BAD3200"].effective_qty, rows["BAD3200"].anomaly) == (
        "custom", 1, "qty_gt3"
    )
    assert rows["SUFFIX99"].kind == "custom"


def test_confirmed_bulk_order_keeps_original_quantity(db_session):
    db = db_session
    _order(db, "REAL-BULK-4", qty=4)
    rows = demand.load_observations(
        db,
        start=AS_OF - timedelta(days=1),
        end=AS_OF,
        cfg={"confirmed_bulk_order_nos": ["REAL-BULK-4"]},
    )
    row = next(x for x in rows if x.order_no == "REAL-BULK-4")
    assert (row.kind, row.effective_qty, row.anomaly) == ("standard", 4, None)


def test_promo_is_normalized_and_cny_is_retained_separately(db_session):
    db = db_session
    _order(db, "NORMAL", qty=1, day=date(2026, 7, 10))
    _order(db, "618", qty=3, day=date(2026, 6, 10))
    _order(db, "CNY", qty=2, day=date(2026, 2, 17))
    observations = demand.load_observations(
        db, start=date(2026, 1, 1), end=AS_OF
    )
    profile = demand.build_profile(observations, as_of=AS_OF)
    # 618 的 3 件按 3 倍活动去峰后只计 1 件；春节不进入普通基线，但仍保留场景量。
    assert profile["window_units"]["90"] == 2
    assert profile["cny_units"] == 2
    assert profile["cny_daily"] > 0


def test_monthly_plan_and_feishu_idempotency(db_session, monkeypatch):
    db = db_session
    for i in range(8):
        _order(
            db,
            f"HOT{i}",
            product_code="PPS26010020202",
            sku_code="PPS2601002020211",
            name="榉木床头柜",
            day=AS_OF - timedelta(days=i),
        )
    db.add(ProductInventory(
        warehouse="default",
        product_code="PPS26010020202",
        sku="标准款",
        physical_qty=Decimal("0"),
        locked_qty=Decimal("0"),
    ))
    settings_service.set_value(db, "feishu_push_chat_id", "test-chat")
    sent = []

    def fake_send(_db, receive_id, text):
        sent.append((receive_id, text))
        return {"data": {"message_id": "m-1"}}

    monkeypatch.setattr(monthly.feishu_client, "send_text", fake_send)
    plan = monthly.build_monthly_plan(db, year=2026, month=8, as_of=AS_OF)
    hot = next(x for x in plan["products"] if x["product_code"] == "PPS26010020202")
    assert hot["qualified_hot"] is True
    assert hot["suggested_restock"] > 0

    first = monthly.send_monthly_report(db, today=date(2026, 7, 31))
    second = monthly.send_monthly_report(db, today=date(2026, 8, 1))
    assert first["pushed"] is True and first["period"] == "2026-08"
    assert second == {
        "period": "2026-08", "pushed": False, "skipped": "already_sent"
    }
    assert len(sent) == 1


def test_abc_uses_cleaned_quantity_not_raw_3200(db_session):
    db = db_session
    _order(db, "BAD3200", qty=3200, product_code="PPS26010030303")
    for i in range(6):
        _order(
            db, f"REAL{i}", product_code="PPS26010040404",
            sku_code="PPS2601004040411", day=AS_OF - timedelta(days=i),
        )
    cfg = product_inventory_service.get_forecast_config(db)
    abc = product_inventory_service.compute_abc_map(db, cfg)
    assert "P26010030303" not in abc
    assert abc["P26010040404"] == "A"


def test_inventory_orders_and_monthly_share_final_restock_number(db_session):
    db = db_session
    for i in range(12):
        _order(
            db,
            f"ONE{i}",
            product_code="PPS26010050505",
            sku_code="PPS2601005050511",
            name="榉木床头柜",
            day=AS_OF - timedelta(days=i),
        )
    inv = ProductInventory(
        warehouse="default",
        product_code="PPS26010050505",
        sku="标准款",
        physical_qty=Decimal("0"),
        locked_qty=Decimal("0"),
    )
    inv_secondary = ProductInventory(
        warehouse="secondary",
        product_code="PPS26010050505",
        sku="备用款",
        physical_qty=Decimal("0"),
        locked_qty=Decimal("0"),
    )
    db.add_all([inv, inv_secondary])
    db.flush()

    plan = restock.build_restock_plan(
        db,
        start=AS_OF + timedelta(days=1),
        end=AS_OF + timedelta(days=30),
        as_of=AS_OF,
    )
    canonical = next(
        x for x in plan["products"] if x["product_code"] == "PPS26010050505"
    )
    advice = sales_analytics.stock_advice(db)
    order_page = next(
        x for x in advice["products"] if x["product_code"] == "PPS26010050505"
    )
    restock_map, allocation = (
        product_inventory_service.build_inventory_restock_context(
            db, [inv, inv_secondary]
        )
    )
    allocated_total = allocation[id(inv)] + allocation[id(inv_secondary)]
    stats = product_inventory_service.compute_product_stats(
        db,
        inv,
        restock_plan_row=restock_map[canonical["product_core"]],
        restock_qty=allocation[id(inv)],
    )
    month = monthly.build_monthly_plan(
        db, year=2026, month=8, as_of=AS_OF
    )
    month_row = next(
        x for x in month["products"] if x["product_code"] == "PPS26010050505"
    )
    assert canonical["suggested_restock"] > 0
    assert allocated_total == canonical["suggested_restock"]
    assert order_page["need_to_produce"] == canonical["suggested_restock"]
    assert stats["auto_reorder_qty"] == allocation[id(inv)]
    assert month_row["suggested_restock"] == canonical["suggested_restock"]

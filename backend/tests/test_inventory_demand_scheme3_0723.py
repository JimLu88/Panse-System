from datetime import date, timedelta
from decimal import Decimal

from app.models.inventory import ProductInventory
from app.models.order import Order
from app.services import (
    inventory_demand_service as demand,
    inventory_monthly_report_service as monthly,
    product_inventory_service,
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
    _order(db, "WARN5", qty=5)
    _order(db, "BAD3200", qty=3200)
    _order(db, "SUFFIX99", qty=2, sku_code="PPS2601001010199")
    rows = {
        row.order_no: row
        for row in demand.load_observations(
            db, start=AS_OF - timedelta(days=1), end=AS_OF
        )
    }
    assert (rows["NORMAL3"].kind, rows["NORMAL3"].effective_qty) == ("standard", 3)
    assert (rows["WARN5"].kind, rows["WARN5"].effective_qty, rows["WARN5"].anomaly) == (
        "standard", 5, "qty_4_5"
    )
    assert (rows["BAD3200"].kind, rows["BAD3200"].effective_qty, rows["BAD3200"].anomaly) == (
        "custom", 1, "qty_gt5"
    )
    assert rows["SUFFIX99"].kind == "custom"


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

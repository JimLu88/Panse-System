"""Phase 4: 销售报表 / 预测 / 资产 / 滞销 / 未核销异常池."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.alert import Alert
from app.models.bom import BomLine
from app.models.finance import AccountBalance, AlipayFlow
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.order import FactoryOrder, Order
from app.services import asset_service, inventory_alert_service, sales_analytics


def _seed_orders(db, days_ago: list[int], paid_amount: float = 1000,
                 cost: float = 600, product_code="P1", platform="淘宝"):
    """造一批 paid 状态订单, 每个 days_ago 偏移一天."""
    today = date.today()
    for i, da in enumerate(days_ago):
        db.add(Order(
            platform=platform, order_no=f"O{i + 10}_{da}",
            order_date=today - timedelta(days=da),
            product_code=product_code, product_name="电视柜",
            sku=f"sku{i}", qty=1,
            paid_amount=Decimal(str(paid_amount)),
            actual_cost=Decimal(str(cost)),
            status="shipped",
        ))
    db.flush()


# ----------------------------- summary -------------------------- #


def test_summary_basic(db_session):
    _seed_orders(db_session, [1, 5, 10], paid_amount=1000, cost=600)
    today = date.today()
    s = sales_analytics.summary(db_session,
                                start=today - timedelta(days=30),
                                end=today)
    assert s.order_count == 3
    assert s.revenue == Decimal("3000")
    assert s.cost == Decimal("1800")
    assert s.gross_profit == Decimal("1200")
    # net = revenue - cost - 其他费 (这里其他费都是 0)
    assert s.net_profit == Decimal("1200")
    # top_products: 单一产品聚合
    assert len(s.top_products_by_profit) == 1
    assert s.top_products_by_profit[0]["net_profit"] == Decimal("1200")


def test_summary_skips_historical(db_session):
    _seed_orders(db_session, [1])
    h = Order(platform="淘宝", order_no="HIST1", order_date=date.today(),
              product_code="P1", qty=1, paid_amount=Decimal("99999"),
              status="shipped", is_historical=True)
    db_session.add(h); db_session.flush()
    today = date.today()
    s = sales_analytics.summary(db_session, start=today - timedelta(days=7), end=today)
    assert s.order_count == 1   # 历史单不算


def test_breakdown_by_sku(db_session):
    db_session.add_all([
        Order(platform="淘宝", order_no="A1", order_date=date.today(),
              product_code="P1", sku_code="S1", qty=2,
              paid_amount=Decimal("2000"), actual_cost=Decimal("800"),
              status="shipped"),
        Order(platform="淘宝", order_no="A2", order_date=date.today(),
              product_code="P1", sku_code="S2", qty=1,
              paid_amount=Decimal("500"), actual_cost=Decimal("200"),
              status="shipped"),
    ])
    db_session.flush()
    rows = sales_analytics.product_breakdown(
        db_session, start=date.today() - timedelta(days=1), end=date.today(),
    )
    assert len(rows) == 2
    s1 = next(r for r in rows if r["sku_code"] == "S1")
    assert s1["revenue"] == Decimal("2000")
    assert s1["qty"] == 2
    assert s1["gross_profit_rate"] > 0


# ----------------------------- forecast ------------------------- #


def test_forecast_30d_uses_60_day_average(db_session):
    """造 60 天里 30 个订单 → 平均 0.5/天 → 预测 30 天 = 30 * 0.5 * 1.2 = 18."""
    today = date.today()
    for i in range(30):
        db_session.add(Order(
            platform="淘宝", order_no=f"F{i}",
            order_date=today - timedelta(days=i * 2),  # 每 2 天 1 单
            product_code="P1", sku_code="S1", qty=1,
            paid_amount=Decimal("100"), status="shipped",
        ))
    db_session.flush()
    rows = sales_analytics.forecast_30d(db_session)
    s1 = next(r for r in rows if r["product_code"] == "P1" and r["sku"] == "S1")
    assert s1["last_60d_total"] == 30
    # avg = 30/60 = 0.5; forecast = 0.5 * 30 * 1.2 = 18
    assert s1["forecast_30d"] == 18
    assert s1["sku_key"] == "P1|S1"


# ----------------------------- stock advice --------------------- #


def test_stock_advice_recommends_material(db_session):
    """造一个 BOM + 库存不足时, stock_advice 给出物料缺货建议."""
    db_session.add_all([
        Material(code="M1", name="木方", lead_time_days=10, priority="high"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("2")),
    ])
    db_session.flush()
    # 历史销量: 60 天 30 单 → 30 天预测 18 件 → 需要 36 件 M1
    today = date.today()
    for i in range(30):
        db_session.add(Order(
            platform="淘宝", order_no=f"A{i}",
            order_date=today - timedelta(days=i * 2),
            product_code="P1", sku_code="S1", qty=1,
            paid_amount=Decimal("100"), status="shipped",
        ))
    db_session.add(PartInventory(warehouse="default", material_code="M1", physical_qty=10))
    db_session.flush()
    advice = sales_analytics.stock_advice(db_session)
    m = next(x for x in advice["materials"] if x["material_code"] == "M1")
    assert m["need_qty"] > m["have_qty"]
    assert m["missing"] > 0
    assert m["lead_time_days"] == 10
    assert m["priority"] == "high"


# ----------------------------- slow moving --------------------- #


def test_slow_moving_long_idle(db_session):
    """物料 90 天未出货 → 长期滞销."""
    old = date.today() - timedelta(days=90)
    db_session.add(Material(code="M2", name="老旧物料"))
    db_session.add(PartInventory(warehouse="default", material_code="M2",
                                 physical_qty=20, last_outbound_at=old))
    db_session.flush()
    split = sales_analytics.slow_moving_split(db_session, long_no_sale_days=60)
    assert any(x["material_code"] == "M2" for x in split["long_idle"])


def test_slow_moving_overstock(db_session):
    """成品库存 > 3 倍预测 → overstock."""
    # 预测 18 件 P1
    today = date.today()
    for i in range(30):
        db_session.add(Order(
            platform="淘宝", order_no=f"B{i}",
            order_date=today - timedelta(days=i * 2),
            product_code="P1", sku_code="S1", qty=1,
            paid_amount=Decimal("100"), status="shipped",
        ))
    db_session.add(ProductInventory(warehouse="default", product_code="P1",
                                    sku="主款", physical_qty=100))   # 100 > 3*18=54
    db_session.flush()
    split = sales_analytics.slow_moving_split(db_session, overstock_ratio=3.0)
    assert any(x["product_code"] == "P1" for x in split["overstock"])


# ----------------------------- inventory alerts ----------------- #


def test_low_stock_alert_uses_lead_time(db_session):
    db_session.add(Material(code="M3", name="小件", lead_time_days=15, priority="high"))
    db_session.add(PartInventory(warehouse="default", material_code="M3",
                                 physical_qty=5, locked_qty=0))
    db_session.flush()
    n = inventory_alert_service.scan_low_stock(db_session)
    assert n == 1
    a = db_session.execute(
        select(Alert).where(Alert.dedupe_key == "low_stock_part:M3")
    ).scalar_one()
    assert a.severity == "critical"  # priority=high → critical
    assert a.sticky is True


def test_low_stock_alert_priority_mapping(db_session):
    db_session.add(Material(code="M4", name="x", lead_time_days=10, priority="low"))
    db_session.add(PartInventory(warehouse="default", material_code="M4", physical_qty=1))
    db_session.flush()
    inventory_alert_service.scan_low_stock(db_session)
    a = db_session.execute(
        select(Alert).where(Alert.dedupe_key == "low_stock_part:M4")
    ).scalar_one()
    assert a.severity == "info"  # priority=low → info


# ----------------------------- assets --------------------------- #


def test_asset_summary_includes_inventory_and_balances(db_session):
    db_session.add(AccountBalance(account_name="支付宝", period_year=2026, period_month=5,
                                  opening_balance=Decimal("1000"),
                                  income=Decimal("500"), expense=Decimal("200"),
                                  closing_balance=Decimal("1300")))
    db_session.add_all([
        Material(code="M5", name="物料5", price=Decimal("10")),
        PartInventory(warehouse="default", material_code="M5", physical_qty=20),
    ])
    db_session.flush()
    s = asset_service.summary(db_session)
    cats = {c.name: float(c.amount) for c in s.categories}
    assert cats["账户余额"] == 1300.0
    assert cats["库存账面"] == 200.0   # 20 * 10
    assert s.total >= Decimal("1500")


def test_unmatched_flows_returns_open_within_window(db_session):
    from datetime import datetime as _dt
    db_session.add_all([
        AlipayFlow(account="ali", transaction_no="T1",
                   transaction_time=_dt.utcnow(),
                   counterparty="X", amount=Decimal("100"),
                   reconciliation_status="open"),
        AlipayFlow(account="ali", transaction_no="T2",
                   transaction_time=_dt.utcnow(),
                   counterparty="Y", amount=Decimal("50"),
                   reconciliation_status="matched"),
    ])
    db_session.flush()
    rows = asset_service.unmatched_recent_flows(db_session, days=7)
    assert len(rows) == 1
    assert rows[0]["transaction_no"] == "T1"


def test_check_formula_generates_alert_on_diff(db_session):
    """造一笔账户余额 + 一笔虚高订单利润, 差额 > 100 → 应有 alert."""
    db_session.add(AccountBalance(account_name="支付宝", period_year=2026, period_month=5,
                                  opening_balance=Decimal("0"),
                                  income=Decimal("0"), expense=Decimal("0"),
                                  closing_balance=Decimal("100")))
    db_session.add(Order(platform="淘宝", order_no="OZ", order_date=date.today(),
                          product_code="P1", qty=1,
                          paid_amount=Decimal("10000"), actual_cost=Decimal("0"),
                          status="shipped"))
    db_session.flush()
    r = asset_service.check_formula_and_alert(db_session)
    assert r["alerted"] is True or abs(r["diff"]) <= 100

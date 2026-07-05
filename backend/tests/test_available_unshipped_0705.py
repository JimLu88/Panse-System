"""成品「可用」口径 = 现货 − 已付未发单 (用户 2026-07-05)。

用户诉求: 现货已被现有订单占用 → 可用不该还显示有货。改为 可用=现货−已付未发(可负)。
发货后 ship_date 一填, 该单自动掉出「已付未发」→ 可用实时回补(负9→负8), 无需额外逻辑。
备货推荐 auto_reorder 仍走物理口径(现货−locked), 不受未发单影响, 防超卖单在产致过量下单。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.inventory import ProductInventory
from app.services import product_inventory_service as pis


def _stats(db, inv):
    cfg = pis.get_forecast_config(db)
    return pis.compute_product_stats(db, inv, abc_map={}, cfg=cfg,
                                     in_production_split=({}, {}))


def _order(db, no, *, code="P1", sku="主款", qty=1, custom=False, refill=False,
           ship=None, paid=Decimal("100"), status="paid", odate=None):
    from app.models.order import Order
    db.add(Order(platform="淘宝", order_no=no, product_code=code, sku_code="S1",
                 sku=sku, qty=qty, is_custom=custom, is_refill=refill,
                 ship_date=ship, paid_amount=paid, status=status,
                 order_date=odate or date.today()))


def test_available_deducts_unshipped_paid_orders(db_session):
    db = db_session
    for i in range(5):
        _order(db, f"U{i}")                 # 5 单 已付未发
    inv = ProductInventory(warehouse="default", product_code="P1", sku="主款",
                           physical_qty=Decimal("3"))
    db.add(inv); db.flush()
    s = _stats(db, inv)
    assert s["physical_available"] == 3
    assert s["unshipped_demand"] == 5
    assert s["available_qty"] == -2         # 3 − 5, 负数正常(用户认可)


def test_shipped_custom_cancelled_refill_not_counted(db_session):
    db = db_session
    _order(db, "SHIP", ship=date.today())                    # 已发 → 不算
    _order(db, "CUST", custom=True)                          # 定制(现产不吃现货) → 不算
    _order(db, "CANCEL", status="cancelled")                 # 取消 → 不算
    _order(db, "PEND", status="pending_payment", paid=None)  # 待付款 → 不算
    _order(db, "REFILL", refill=True)                        # 补单(刷单) → 不算
    _order(db, "OK1"); _order(db, "OK2")                     # 2 单 真·已付未发 → 算
    inv = ProductInventory(warehouse="default", product_code="P1", sku="主款",
                           physical_qty=Decimal("10"))
    db.add(inv); db.flush()
    s = _stats(db, inv)
    assert s["unshipped_demand"] == 2
    assert s["available_qty"] == 8


def test_reorder_ignores_unshipped_uses_physical(db_session):
    """加未发单只压低『展示可用』, 不动『备货推荐』(推荐走物理口径, 防过量下单)。"""
    db = db_session
    inv = ProductInventory(warehouse="default", product_code="P1", sku="主款",
                           physical_qty=Decimal("5"),
                           reorder_point=Decimal("10"))   # 手填预警线 → 强制备货口(do_stock)
    db.add(inv); db.flush()
    base = _stats(db, inv)                 # 无未发单: 展示可用=5, 触发备货(5<10)
    assert base["available_qty"] == 5
    assert base["auto_reorder_qty"] > 0
    # 塞 20 单已付未发; 下单日落在销量窗口外(200天前)以隔离变量: 只压可用, 不抬销量预测。
    old = date.today() - timedelta(days=200)
    for i in range(20):
        _order(db, f"U{i}", odate=old)
    db.flush()
    after = _stats(db, inv)
    assert after["available_qty"] == base["available_qty"] - 20   # 展示可用降 20 (=-15)
    assert after["auto_reorder_qty"] == base["auto_reorder_qty"]  # 备货推荐不变(走物理5, 没因负可用暴增)


def test_unshipped_scoped_by_size_token(db_session):
    """同产品多尺寸: 未发单按 sku 尺寸口令分摊, 只压对应尺寸的可用。"""
    db = db_session
    _order(db, "A1", sku="砂白1.6米餐桌")
    _order(db, "A2", sku="砂白1.6米餐桌")
    _order(db, "B1", sku="砂白1.4米餐桌")
    inv16 = ProductInventory(warehouse="default", product_code="P1", sku="餐桌-1.6米",
                             physical_qty=Decimal("1"))
    inv14 = ProductInventory(warehouse="default", product_code="P1", sku="餐桌-1.4米",
                             physical_qty=Decimal("1"))
    db.add_all([inv16, inv14]); db.flush()
    s16 = _stats(db, inv16)
    s14 = _stats(db, inv14)
    assert s16["unshipped_demand"] == 2 and s16["available_qty"] == -1   # 1 − 2
    assert s14["unshipped_demand"] == 1 and s14["available_qty"] == 0    # 1 − 1


def test_shipped_auto_replenishes_available(db_session):
    """发货即回补: 一单从未发→已发(填 ship_date), 可用自动 +1 (负→回升)。"""
    db = db_session
    for i in range(3):
        _order(db, f"U{i}")
    inv = ProductInventory(warehouse="default", product_code="P1", sku="主款",
                           physical_qty=Decimal("1"))
    db.add(inv); db.flush()
    before = _stats(db, inv)
    assert before["available_qty"] == -2        # 1 − 3
    from app.models.order import Order
    o = db.query(Order).filter(Order.order_no == "U0").one()
    o.ship_date = date.today() - timedelta(days=0)   # 发货
    db.flush()
    after = _stats(db, inv)
    assert after["available_qty"] == -1         # 掉出未发 → 可用 +1

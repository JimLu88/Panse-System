"""Phase 2: 平台 Order → FactoryOrder → 库存锁定 联动流程."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.alert import Alert
from app.models.bom import BomLine
from app.models.inventory import PartInventory
from app.models.material import Material
from app.models.order import FactoryOrder, Order
from app.services import factory_order_service, order_service


def _setup(db, bom_qty: int = 10):
    db.add_all([
        Material(code="M001", name="木方"),
        Material(code="M002", name="五金"),
    ])
    db.flush()
    db.add_all([
        BomLine(product_code="P1", material_code="M001", qty_per_product=Decimal("2")),
        BomLine(product_code="P1", material_code="M002", qty_per_product=Decimal("1")),
    ])
    db.add_all([
        PartInventory(warehouse="default", material_code="M001", physical_qty=bom_qty),
        PartInventory(warehouse="default", material_code="M002", physical_qty=bom_qty),
    ])
    db.flush()


def _make_order(db, status="pending_payment", qty=1, **kw):
    o = Order(
        platform="淘宝", order_no=f"O{id(db) % 10000 + qty + len(kw)}",
        product_code="P1", qty=qty, status=status, **kw,
    )
    db.add(o); db.flush()
    return o


# ----------------------------- 生成 ----------------------------- #


def test_generate_factory_order_locks_inventory(db_session):
    _setup(db_session, bom_qty=10)
    o = _make_order(db_session, qty=2)
    fo, lock = factory_order_service.generate_factory_order_for(db_session, o)
    assert fo.source_order_id == o.id
    assert fo.qty == 2
    assert lock.shortages == []
    # 锁定 M001 = 2*2=4, M002 = 1*2=2
    m1 = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M001")
    ).scalar_one()
    assert m1.locked_qty == 4


def test_generate_is_idempotent(db_session):
    _setup(db_session)
    o = _make_order(db_session)
    fo1, _ = factory_order_service.generate_factory_order_for(db_session, o)
    fo2, _ = factory_order_service.generate_factory_order_for(db_session, o)
    assert fo1.id == fo2.id


def test_generate_rejects_historical(db_session):
    _setup(db_session)
    o = _make_order(db_session, is_historical=True)
    import pytest
    with pytest.raises(ValueError):
        factory_order_service.generate_factory_order_for(db_session, o)


def test_shortage_creates_critical_alert(db_session):
    _setup(db_session, bom_qty=1)   # 库存只有 1 件, 不够下 2 件订单
    o = _make_order(db_session, qty=2)
    _, lock = factory_order_service.generate_factory_order_for(db_session, o)
    assert len(lock.shortages) >= 1
    alerts = db_session.execute(
        select(Alert).where(Alert.severity == "critical", Alert.kind == "low_stock_part")
    ).scalars().all()
    assert len(alerts) >= 1


# ----------------------------- 取消 ----------------------------- #


def test_cancel_releases_lock(db_session):
    _setup(db_session)
    o = _make_order(db_session)
    factory_order_service.generate_factory_order_for(db_session, o)
    db_session.flush()

    n = factory_order_service.cancel_factory_orders_for(db_session, o)
    assert n == 1
    # 锁定归零
    m1 = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M001")
    ).scalar_one()
    assert m1.locked_qty == 0
    # 工厂单 voided
    fo = db_session.execute(
        select(FactoryOrder).where(FactoryOrder.source_order_id == o.id)
    ).scalar_one()
    assert fo.voided_at is not None


def test_void_specific_factory_order(db_session):
    _setup(db_session)
    o = _make_order(db_session)
    fo, _ = factory_order_service.generate_factory_order_for(db_session, o)
    factory_order_service.void_factory_order(db_session, fo.id, reason="客户改单")
    db_session.refresh(fo)
    assert fo.voided_at is not None
    assert fo.voided_reason == "客户改单"


# ----------------------------- 出货 ----------------------------- #


def test_ship_consumes_inventory(db_session):
    _setup(db_session, bom_qty=10)
    o = _make_order(db_session)
    factory_order_service.generate_factory_order_for(db_session, o)
    db_session.flush()
    n = factory_order_service.ship_factory_orders_for(db_session, o)
    assert n == 1
    m1 = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M001")
    ).scalar_one()
    assert m1.physical_qty == 8   # 10 - 2
    assert m1.locked_qty == 0
    assert o.last_outbound_at is not None


# ----------------------------- 状态机联动 ----------------------- #


def test_transition_to_paid_auto_generates(db_session):
    _setup(db_session)
    o = _make_order(db_session, status="pending_payment")
    order_service.transition(db_session, o, "paid", actor="cs")
    # 应自动派生工厂单
    fo = db_session.execute(
        select(FactoryOrder).where(FactoryOrder.source_order_id == o.id)
    ).scalar_one()
    assert fo is not None
    # 锁定生效
    m1 = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M001")
    ).scalar_one()
    assert m1.locked_qty > 0


def test_transition_to_cancelled_releases(db_session):
    _setup(db_session)
    o = _make_order(db_session, status="pending_payment")
    order_service.transition(db_session, o, "paid", actor="cs")
    db_session.flush()
    order_service.transition(db_session, o, "cancelled", actor="cs")
    m1 = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M001")
    ).scalar_one()
    assert m1.locked_qty == 0


def test_transition_to_shipped_consumes(db_session):
    _setup(db_session)
    o = _make_order(db_session, status="pending_payment")
    order_service.transition(db_session, o, "paid", actor="cs")
    db_session.flush()
    order_service.transition(db_session, o, "shipped", actor="cs")
    m1 = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M001")
    ).scalar_one()
    assert m1.physical_qty == 8   # 10 - 2


def test_historical_orders_skip_factory_flow(db_session):
    _setup(db_session)
    o = _make_order(db_session, status="pending_payment", is_historical=True)
    order_service.transition(db_session, o, "paid", actor="cs", force=True)
    # 没有 FactoryOrder 派生
    fo = db_session.execute(
        select(FactoryOrder).where(FactoryOrder.source_order_id == o.id)
    ).scalar_one_or_none()
    assert fo is None
    # 库存没动
    m1 = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M001")
    ).scalar_one()
    assert m1.locked_qty == 0


# ----------------------------- 远期订单 ------------------------- #


def test_create_future_order(db_session):
    activate = datetime.now(timezone.utc) + timedelta(days=30)
    o = factory_order_service.create_future_order(
        db_session, base_order_no="A1", activate_at=activate,
        product_code="P1", qty=1,
    )
    assert o.activate_at is not None
    assert o.status == "pending_payment"
    assert "_FUT_" in o.order_no


def test_scheduler_activates_future(db_session, monkeypatch):
    """daily_08_activate_future 任务: 过期的远期订单 → status=paid."""
    from app import database as db_module
    from app.services import scheduler as scheduler_service
    bind = db_session.get_bind()
    from sqlalchemy.orm import sessionmaker
    LocalSm = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_module, "SessionLocal", LocalSm)

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    o = Order(platform="淘宝", order_no="FUT1", status="pending_payment",
              activate_at=past, product_code="P1")
    db_session.add(o); db_session.commit()

    scheduler_service._REGISTRY.clear()
    scheduler_service._SCHEDULER = None
    scheduler_service.register_job(
        "activate", "激活", scheduler_service._job_activate_future_orders,
        cron={"hour": 8, "minute": 0},
    )
    scheduler_service.trigger_now("activate")

    db_session.expire_all()
    refreshed = db_session.get(Order, o.id)
    assert refreshed.status == "paid"


# ----------------------------- 17:00 退款检查 ------------------- #


def test_refund_check_flags_aged_aftersales(db_session):
    """超过 24h 仍在 aftersales 且有 compensation 的, flag 一条 Alert."""
    past = datetime.now(timezone.utc) - timedelta(hours=48)
    o = Order(platform="淘宝", order_no="R1", status="aftersales",
              compensation_fee=Decimal("100"))
    db_session.add(o); db_session.flush()
    # 模拟订单上次更新发生在 48 小时前
    o.updated_at = past
    db_session.flush()

    result = factory_order_service.check_refund_pending_orders(db_session)
    assert result["flagged"] >= 1
    a = db_session.execute(
        select(Alert).where(Alert.kind == "refund_pending")
    ).scalar_one()
    assert a.dedupe_key == f"refund_pending:{o.order_no}"


def test_missing_tracking_check_creates_sticky_alert(db_session):
    from app.models.order import PartPurchase
    from datetime import date as _date, timedelta as _td
    db_session.add(PartPurchase(
        purchase_no="PP1", supplier="S1", purchase_date=_date.today() - _td(days=2),
        material_code="M001", material_name="木方",
    ))
    db_session.flush()
    result = factory_order_service.check_missing_tracking(db_session)
    assert result["missing_tracking_count"] >= 1
    a = db_session.execute(
        select(Alert).where(Alert.kind == "missing_tracking")
    ).scalar_one()
    assert a.sticky is True

"""Phase 8 Tier 1: AI 简报 / 订单时间轴 / 会计期间 / 供应商评分."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.accounting_period import AccountingPeriod
from app.models.bom import BomLine
from app.models.daily_briefing import DailyBriefing
from app.models.inventory import PartInventory
from app.models.material import Material
from app.models.order import FactoryOrder, Order, PartPurchase
from app.models.order_event import OrderEvent
from app.models.supplier import Supplier
from app.models.supplier_score import SupplierScore
from app.services import (
    accounting_period_service,
    briefing_service,
    order_event_service,
    order_service,
    supplier_score_service,
)
from app.services.ai_provider import AiResponse


# ============================ 订单时间轴 ============================ #


def test_status_change_writes_event(db_session):
    o = Order(platform="淘宝", order_no="OE1", qty=1, status="pending_payment")
    db_session.add(o); db_session.flush()
    order_service.transition(db_session, o, "paid", actor="alice")
    events = order_event_service.list_for_order(db_session, o.id)
    kinds = [e.kind for e in events]
    assert "status_change" in kinds
    sc = [e for e in events if e.kind == "status_change"][0]
    assert "pending_payment" in sc.summary
    assert "paid" in sc.summary
    assert sc.actor == "alice"


def test_factory_generation_writes_event(db_session):
    db_session.add_all([
        Material(code="M1", name="x"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("1")),
        PartInventory(warehouse="default", material_code="M1", physical_qty=Decimal("10")),
    ])
    db_session.flush()
    o = Order(platform="淘宝", order_no="OE2", product_code="P1", qty=1,
              status="pending_payment")
    db_session.add(o); db_session.flush()
    order_service.transition(db_session, o, "paid", actor="cs")
    events = order_event_service.list_for_order(db_session, o.id)
    kinds = {e.kind for e in events}
    assert "factory_order_generated" in kinds


def test_factory_cancel_writes_event(db_session):
    db_session.add_all([
        Material(code="M1", name="x"),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("1")),
        PartInventory(warehouse="default", material_code="M1", physical_qty=Decimal("10")),
    ])
    db_session.flush()
    o = Order(platform="淘宝", order_no="OE3", product_code="P1", qty=1,
              status="pending_payment")
    db_session.add(o); db_session.flush()
    order_service.transition(db_session, o, "paid", actor="cs")
    order_service.transition(db_session, o, "cancelled", actor="cs")
    events = order_event_service.list_for_order(db_session, o.id)
    kinds = [e.kind for e in events]
    assert "factory_order_voided" in kinds


# ============================ 会计期间 ============================ #


def test_close_period_blocks_writes(db_session):
    accounting_period_service.close_period(db_session, 2026, 1, actor="admin")
    with pytest.raises(accounting_period_service.PeriodLocked):
        accounting_period_service.ensure_writable(db_session, date(2026, 1, 15))


def test_open_period_allows_writes(db_session):
    accounting_period_service.close_period(db_session, 2026, 1)
    accounting_period_service.reopen_period(db_session, 2026, 1)
    accounting_period_service.ensure_writable(db_session, date(2026, 1, 15))  # 不抛


def test_lock_period_cannot_reopen(db_session):
    accounting_period_service.lock_period(db_session, 2026, 1)
    with pytest.raises(accounting_period_service.PeriodLocked):
        accounting_period_service.reopen_period(db_session, 2026, 1)


def test_no_period_means_writable(db_session):
    # 没建过期间的月份默认可写
    accounting_period_service.ensure_writable(db_session, date(2050, 6, 1))


# ============================ AI 简报 ============================ #


def test_briefing_generates_with_fallback(db_session):
    """AI 未配置时 fallback."""
    db_session.add(Order(platform="淘宝", order_no="B1",
                          order_date=date.today() - timedelta(days=1),
                          qty=1, paid_amount=Decimal("1000"), actual_cost=Decimal("400"),
                          status="shipped"))
    db_session.flush()
    b = briefing_service.generate(db_session, push=False)
    assert b.model == "fallback"
    assert "成交" in b.content or "营收" in b.content


def test_briefing_with_ai_call(db_session):
    db_session.add(Order(platform="淘宝", order_no="B2",
                          order_date=date.today() - timedelta(days=1),
                          qty=1, paid_amount=Decimal("999"), status="shipped"))
    db_session.flush()
    class FakeProv:
        name, model = "anthropic", "claude-opus"
        def chat(self, **kw):
            return AiResponse(text="昨日营收 ¥999, 净利 ¥500. · 风险: 无.",
                              model="claude-opus")
    with patch("app.services.briefing_service.build_provider", return_value=FakeProv()):
        b = briefing_service.generate(db_session, push=False)
    assert b.model == "claude-opus"
    assert "999" in b.content


def test_briefing_upsert_same_day(db_session):
    db_session.add(Order(platform="淘宝", order_no="B3",
                          order_date=date.today() - timedelta(days=1),
                          qty=1, status="shipped"))
    db_session.flush()
    target = date.today() - timedelta(days=1)
    b1 = briefing_service.generate(db_session, target, push=False)
    b2 = briefing_service.generate(db_session, target, push=False)
    assert b1.id == b2.id   # 同一日复用


def test_briefing_highlights(db_session):
    """超快预测/缺货物料应进 highlights."""
    db_session.add(Material(code="M1", name="x", lead_time_days=20))
    db_session.add(BomLine(product_code="P1", material_code="M1",
                            qty_per_product=Decimal("1")))
    db_session.add(PartInventory(warehouse="default", material_code="M1",
                                  physical_qty=Decimal("0")))
    # 造点销售历史
    today = date.today()
    for i in range(30):
        db_session.add(Order(platform="淘宝", order_no=f"H{i}",
                              order_date=today - timedelta(days=i * 2),
                              product_code="P1", sku_code="S1",
                              qty=1, paid_amount=Decimal("100"), status="shipped"))
    db_session.flush()
    b = briefing_service.generate(db_session, push=False)
    assert b.highlights_json is not None
    assert any(h.get("kind") == "risk" for h in b.highlights_json)


# ============================ 供应商评分 ============================ #


def test_supplier_score_basic(db_session):
    db_session.add(Supplier(name="X 工厂", supplier_type="wood", is_active=True))
    db_session.flush()
    target = date.today().replace(day=15) - timedelta(days=30)
    db_session.add(PartPurchase(
        purchase_no="P1", supplier="X 工厂",
        purchase_date=target,
        material_name="木方", qty=Decimal("10"),
        unit_price=Decimal("25"), amount=Decimal("250"),
    ))
    db_session.flush()
    rows = supplier_score_service.compute_for_month(
        db_session, target.year, target.month,
    )
    assert len(rows) == 1
    assert rows[0].total_orders == 1
    assert rows[0].score is not None
    assert rows[0].rank == 1


def test_supplier_score_rank_by_score(db_session):
    """多供应商应正确排名."""
    db_session.add_all([
        Supplier(name="S1", supplier_type="wood", is_active=True),
        Supplier(name="S2", supplier_type="wood", is_active=True),
    ])
    db_session.flush()
    target = date.today().replace(day=15) - timedelta(days=30)
    db_session.add_all([
        PartPurchase(purchase_no=f"P{i}", supplier=s,
                      purchase_date=target, material_name="x",
                      qty=Decimal("1"), unit_price=Decimal("10"),
                      amount=Decimal("10"))
        for i, s in enumerate(["S1", "S2"])
    ])
    db_session.flush()
    rows = supplier_score_service.compute_for_month(
        db_session, target.year, target.month,
    )
    assert len(rows) == 2
    ranks = sorted([r.rank for r in rows])
    assert ranks == [1, 2]

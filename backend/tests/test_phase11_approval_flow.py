"""Phase 11: 审批工作流 + 流水 AI 归类."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.approval import ApprovalRequest
from app.models.finance import AlipayFlow
from app.models.inventory import PartInventory
from app.models.material import Material
from app.models.order import Order
from app.services import approval_service, flow_classification_service
from app.services.ai_provider import AiResponse


# ============================ 审批工作流 ============================ #


def test_create_request_generates_alert(db_session):
    r = approval_service.create_request(
        db_session, kind="order_discount", title="折扣 ¥800",
        payload={"action": "order_discount", "order_id": 1, "new_amount": 4200},
        requester="alice",
    )
    assert r.status == "pending"
    a = db_session.execute(
        select(Alert).where(Alert.kind == "approval_pending")
    ).scalar_one()
    assert a.sticky is True


def test_approve_runs_executor(db_session):
    db_session.add(Material(code="M1", name="x"))
    db_session.add(PartInventory(warehouse="default", material_code="M1",
                                  physical_qty=Decimal("100")))
    db_session.flush()
    r = approval_service.create_request(
        db_session, kind="inventory_adjust", title="盘点 -50",
        payload={"action": "inventory_adjust",
                 "material_code": "M1", "new_physical": 50,
                 "request_id": 1},
        requester="alice",
    )
    approval_service.approve(db_session, r.id, approver="boss")
    db_session.refresh(r)
    assert r.status == "approved"
    assert r.approver == "boss"
    inv = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M1")
    ).scalar_one()
    assert inv.physical_qty == Decimal("50")


def test_cannot_approve_own(db_session):
    r = approval_service.create_request(
        db_session, kind="x", title="t", payload={"action": "noop"},
        requester="alice",
    )
    with pytest.raises(ValueError):
        approval_service.approve(db_session, r.id, approver="alice")


def test_reject_changes_status(db_session):
    r = approval_service.create_request(
        db_session, kind="x", title="t", payload={"action": "noop"},
        requester="alice",
    )
    approval_service.reject(db_session, r.id, approver="boss", reason="不合理")
    db_session.refresh(r)
    assert r.status == "rejected"
    assert r.reject_reason == "不合理"


def test_double_approve_fails(db_session):
    r = approval_service.create_request(
        db_session, kind="x", title="t", payload={"action": "noop"},
        requester="alice",
    )
    approval_service.reject(db_session, r.id, approver="boss", reason="x")
    with pytest.raises(ValueError):
        approval_service.approve(db_session, r.id, approver="boss")


def test_order_discount_executor(db_session):
    o = Order(platform="淘宝", order_no="OD1", qty=1, status="paid",
              paid_amount=Decimal("5000"))
    db_session.add(o); db_session.flush()
    r = approval_service.create_request(
        db_session, kind="order_discount", title="折扣到 3000",
        payload={"action": "order_discount", "order_id": o.id, "new_amount": 3000},
        requester="alice",
    )
    approval_service.approve(db_session, r.id, approver="boss")
    db_session.refresh(o)
    assert o.paid_amount == Decimal("3000")


def test_threshold_helpers():
    assert approval_service.requires_approval_for_discount(
        Decimal("1000"), Decimal("400")) is True
    assert approval_service.requires_approval_for_discount(
        Decimal("1000"), Decimal("700")) is False
    assert approval_service.requires_approval_for_inventory(Decimal("150")) is True
    assert approval_service.requires_approval_for_inventory(Decimal("50")) is False


# ============================ 流水 AI 归类 ============================ #


def test_classify_rule_factory(db_session):
    f = AlipayFlow(account="ali", transaction_no="T1",
                    amount=Decimal("-500"),
                    counterparty="X 木业有限公司",
                    reconciliation_status="open")
    db_session.add(f); db_session.flush()
    r = flow_classification_service.classify_flow(db_session, f.id)
    assert r["kind"] == "factory_payment"


def test_classify_rule_logistics(db_session):
    f = AlipayFlow(account="ali", transaction_no="T2", amount=Decimal("-100"),
                    counterparty="顺丰速运",
                    reconciliation_status="open")
    db_session.add(f); db_session.flush()
    r = flow_classification_service.classify_flow(db_session, f.id)
    assert r["kind"] == "logistics"


def test_classify_rule_unknown(db_session):
    f = AlipayFlow(account="ali", transaction_no="T3", amount=Decimal("-50"),
                    counterparty="某神秘人",
                    reconciliation_status="open")
    db_session.add(f); db_session.flush()
    r = flow_classification_service.classify_flow(db_session, f.id)
    assert r["kind"] == "unknown"


def test_classify_with_ai(db_session):
    f = AlipayFlow(account="ali", transaction_no="T4", amount=Decimal("-500"),
                    counterparty="未知公司",
                    reconciliation_status="open")
    db_session.add(f); db_session.flush()
    fake = '{"kind":"factory_payment","confidence":0.85,"reason":"AI 推断","suggested_actions":[]}'
    class P:
        name, model = "x", "x"
        def chat(self, **kw):
            return AiResponse(text=fake, model="x")
    with patch("app.services.flow_classification_service.build_provider",
               return_value=P()):
        r = flow_classification_service.classify_flow(db_session, f.id)
    assert r["kind"] == "factory_payment"
    assert r["confidence"] == 0.85


def test_batch_classify(db_session):
    db_session.add_all([
        AlipayFlow(account="ali", transaction_no=f"B{i}",
                   amount=Decimal("-100"),
                   counterparty="X 木业" if i % 2 == 0 else "顺丰",
                   transaction_time=datetime.utcnow(),
                   reconciliation_status="open")
        for i in range(4)
    ])
    db_session.flush()
    results = flow_classification_service.batch_classify(db_session, days=7, limit=10)
    assert len(results) == 4
    kinds = [r["kind"] for r in results]
    assert "factory_payment" in kinds
    assert "logistics" in kinds

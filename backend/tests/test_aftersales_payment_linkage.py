from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.aftersales_payment import AfterSalesPaymentLink
from app.models.field_change import FieldChange
from app.models.finance import AlipayFlow, WanshifuOrder
from app.models.marketing import AfterSales
from app.models.order import Order
from app.services import aftersales_finance_service
from app.services import aftersales_payment_link_service as svc


def _order(db, no: str, customer: str, *, status: str = "signed", day=date(2026, 7, 8)) -> Order:
    row = Order(
        platform="淘宝", order_no=no, customer_name=customer, product_name="测试家具",
        status=status, order_date=day, qty=1, paid_amount=Decimal("1000"), refund_amount=Decimal("0"),
    )
    db.add(row)
    db.flush()
    return row


def _flow(db, no: str, remark: str, amount: str = "-3.92", *, day=13) -> AlipayFlow:
    row = AlipayFlow(
        account="主力号", transaction_no=no, amount=Decimal(amount),
        transaction_time=datetime(2026, 8, day, 13, 55, tzinfo=timezone.utc),
        related_order_no="", remark=remark, reconciliation_status="open",
    )
    db.add(row)
    db.flush()
    return row


def test_preview_unique_name_price_difference_is_auto_eligible(db_session):
    order = _order(db_session, "3311172771684177561", "陈二年")
    _flow(db_session, "FLOW-1", "陈二年客户差价补偿 陈二年客户差价补偿")

    rows = svc.preview(db_session, start_date=date(2026, 8, 1), end_date=date(2026, 8, 31))

    assert len(rows) == 1
    assert rows[0].category == "price_difference"
    assert rows[0].order_id == order.id
    assert rows[0].match_method == "customer_name_unique_active"
    assert rows[0].auto_eligible is True


def test_preview_positive_recovery_is_never_aftersales_cost(db_session):
    _order(db_session, "O-1", "赵大鲸鱼")
    _flow(db_session, "FLOW-IN", "赵大鲸鱼物流拦截费", amount="425.00", day=11)
    assert svc.preview(db_session, start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)) == []


def test_structured_related_order_must_exist_before_auto_link(db_session):
    order = _order(db_session, "O-RELATED", "结构化客户")
    flow = _flow(db_session, "FLOW-RELATED", "客户差价补偿")
    flow.related_order_no = order.order_no

    row = svc.preview(db_session)[0]
    assert row.order_id == order.id
    assert row.match_method == "flow_related_order_exact"
    assert row.auto_eligible is True

    flow.related_order_no = "O-MISSING"
    row = svc.preview(db_session)[0]
    assert row.order_id is None
    assert row.match_method == "flow_related_order_missing"
    assert row.auto_eligible is False


def test_same_name_multiple_orders_requires_review(db_session):
    _order(db_session, "O-1", "张思思", day=date(2026, 6, 1))
    _order(db_session, "O-2", "张思思", day=date(2026, 7, 1))
    _flow(db_session, "FLOW-MULTI", "张思思客户图评返")

    row = svc.preview(db_session)[0]
    assert row.order_id is None
    assert row.match_method == "customer_name_multiple"
    assert row.auto_eligible is False
    assert len(row.evidence["candidate_orders"]) == 2


def test_cancelled_only_never_auto_links(db_session):
    _order(db_session, "O-CANCEL", "华画", status="cancelled")
    _flow(db_session, "FLOW-CANCEL", "华画返床头柜")

    row = svc.preview(db_session)[0]
    assert row.match_method == "cancelled_orders_only"
    assert row.auto_eligible is False


def test_onsite_service_and_wanshifu_candidate_require_review(db_session):
    order = _order(db_session, "O-AZ", "阿哲")
    wsf = WanshifuOrder(
        wsf_order_no="P-1", customer_name="阿哲", status="交易成功",
        matched_order_no=order.order_no, net_amount=Decimal("179"),
    )
    db_session.add(wsf)
    _flow(db_session, "FLOW-REPAIR", "阿哲客户床维修费", amount="-150")
    db_session.flush()

    row = svc.preview(db_session)[0]
    assert row.category == "repair_service"
    assert row.wanshifu_order_id == wsf.id
    assert row.auto_eligible is False
    assert "必须复核" in row.reason


def test_normal_delivery_is_suggested_as_order_install(db_session):
    order = _order(db_session, "O-INSTALL", "思阅")
    _flow(db_session, "FLOW-INSTALL", "思阅送装", amount="-180")

    row = svc.preview(db_session)[0]

    assert row.category == "onsite_service"
    assert row.order_id == order.id
    assert row.suggested_accounting_target == "order_install"
    assert row.auto_eligible is False


def test_confirm_order_install_updates_order_without_aftersales_and_voids(db_session):
    order = _order(db_session, "O-INSTALL", "思阅")
    flow = _flow(db_session, "FLOW-INSTALL", "思阅送装", amount="-180")
    scan = svc.persist_scan(db_session)
    link = db_session.get(AfterSalesPaymentLink, scan["link_ids"][0])

    svc.confirm(
        db_session, link.id, expected_version=1, actor="tester",
        accounting_target="order_install",
    )

    assert link.accounting_target == "order_install"
    assert link.after_sales_id is None
    assert order.install_fee == Decimal("180.00")
    assert order.actual_install == Decimal("180.00")
    assert flow.reconciliation_status == "matched"
    assert flow.reconciliation_type == "install"
    assert db_session.query(AfterSales).filter(AfterSales.alipay_flow_no == flow.transaction_no).count() == 0
    changes = db_session.query(FieldChange).filter(FieldChange.row_pk == order.order_no).all()
    assert {row.field for row in changes} == {"install_fee", "actual_install"}
    assert {row.source for row in changes} == {"alipay_link"}
    assert all(len(row.source) <= 16 for row in changes)

    svc.void(db_session, link.id, expected_version=2, actor="admin", decision_note="测试回撤")
    assert order.install_fee is None
    assert order.actual_install is None
    assert flow.reconciliation_status == "open"
    assert flow.reconciliation_type is None


def test_confirm_order_install_never_overwrites_existing_install_cost(db_session):
    order = _order(db_session, "O-INSTALL", "思阅")
    order.install_fee = Decimal("179")
    _flow(db_session, "FLOW-INSTALL", "思阅送装", amount="-180")
    scan = svc.persist_scan(db_session)
    link = db_session.get(AfterSalesPaymentLink, scan["link_ids"][0])

    with pytest.raises(ValueError, match="不能自动覆盖"):
        svc.confirm(
            db_session, link.id, expected_version=1, actor="tester",
            accounting_target="order_install",
        )


def test_manual_repair_can_record_separate_from_prior_wanshifu(db_session):
    order = _order(db_session, "O-AZ", "阿哲")
    wsf = WanshifuOrder(
        wsf_order_no="P-FIRST", customer_name="阿哲", status="交易成功",
        matched_order_no=order.order_no, net_amount=Decimal("179"),
    )
    db_session.add(wsf)
    flow = _flow(db_session, "FLOW-THIRD", "阿哲客户床维修费第三次", amount="-150")
    db_session.flush()
    scan = svc.persist_scan(db_session)
    link = db_session.get(AfterSalesPaymentLink, scan["link_ids"][0])
    assert link.wanshifu_order_id == wsf.id

    svc.confirm(
        db_session, link.id, expected_version=1, actor="tester",
        accounting_target="aftersales", clear_wanshifu=True,
        decision_note="发生在万师傅首修完成后，明确为第三次维修",
    )

    row = db_session.get(AfterSales, link.after_sales_id)
    assert link.wanshifu_order_id is None
    assert row.second_visit_fee == Decimal("150.00")
    assert row.out_platform_total == Decimal("150.00")
    assert flow.reconciliation_type == "aftersales"


def test_persist_auto_confirm_updates_single_authoritative_chain(db_session):
    order = _order(db_session, "O-SAFE", "陈二年")
    flow = _flow(db_session, "FLOW-SAFE", "陈二年客户差价补偿")

    result = svc.persist_scan(
        db_session, start_date=date(2026, 8, 1), end_date=date(2026, 8, 31),
        auto_confirm_safe=True,
    )

    assert result == {
        "scanned": 1, "created": 1, "confirmed": 1,
        "auto_confirm_errors": 0, "link_ids": result["link_ids"],
    }
    link = db_session.get(AfterSalesPaymentLink, result["link_ids"][0])
    aftersales = db_session.get(AfterSales, link.after_sales_id)
    assert link.status == "confirmed"
    assert link.order_id == order.id
    assert aftersales.payment_link_managed is True
    assert aftersales.platform_order_no == order.order_no
    assert aftersales.good_review_refund == Decimal("3.92")
    assert aftersales.out_platform_total == Decimal("3.92")
    assert flow.reconciliation_status == "matched"
    assert flow.reconciliation_type == "aftersales"
    # 再扫一次不建重复候选。
    assert svc.persist_scan(db_session, start_date=date(2026, 8, 1), end_date=date(2026, 8, 31))["created"] == 0


def test_confirm_reuses_safe_legacy_orphan_then_void_restores_flow(db_session):
    order = _order(db_session, "O-ORPHAN", "陈二年")
    flow = _flow(db_session, "FLOW-ORPHAN", "陈二年客户差价补偿")
    orphan = AfterSales(
        platform_order_no="", direct_compensation=Decimal("3.92"),
        alipay_flow_no=flow.transaction_no, status="auto",
        remark=f"自动从支付宝流水 {flow.transaction_no} 生成",
    )
    db_session.add(orphan)
    db_session.flush()
    scan = svc.persist_scan(db_session)
    link = db_session.get(AfterSalesPaymentLink, scan["link_ids"][0])

    svc.confirm(db_session, link.id, expected_version=1, actor="tester")
    assert link.after_sales_id == orphan.id
    assert orphan.platform_order_no == order.order_no
    assert orphan.payment_link_managed is True
    assert orphan.good_review_refund == Decimal("3.92")
    assert orphan.direct_compensation is None

    svc.void(db_session, link.id, expected_version=2, actor="admin", decision_note="测试回撤")
    assert link.status == "voided"
    assert orphan.status == "link_voided"
    assert orphan.out_platform_total is None
    assert flow.reconciliation_status == "open"
    assert flow.reconciliation_type is None


def test_confirm_rejects_stale_version_and_missing_order(db_session):
    _flow(db_session, "FLOW-NO-ORDER", "不存在客户差价")
    scan = svc.persist_scan(db_session)
    link = db_session.get(AfterSalesPaymentLink, scan["link_ids"][0])
    with pytest.raises(ValueError, match="真实存在的订单"):
        svc.confirm(db_session, link.id, expected_version=1, actor="tester")
    with pytest.raises(ValueError, match="候选已变化"):
        svc.confirm(db_session, link.id, expected_version=99, actor="tester", order_no="ANY")


def test_cancelled_exact_order_is_not_auto_confirmed(db_session):
    order = _order(db_session, "1234567890123456789", "取消客户", status="cancelled")
    _flow(db_session, "FLOW-CANCEL-EXACT", f"{order.order_no}客户差价")

    scan = svc.persist_scan(db_session, auto_confirm_safe=True)

    link = db_session.get(AfterSalesPaymentLink, scan["link_ids"][0])
    assert scan["confirmed"] == 0
    assert link.status == "proposed"
    assert "订单已取消" in link.decision_note


def test_finance_total_uses_total_or_breakdown_without_double_count(db_session):
    linked = AfterSales(
        platform_order_no="O1", in_platform_total=Decimal("10"),
        out_platform_total=Decimal("20"), direct_compensation=Decimal("20"),
    )
    legacy = AfterSales(
        platform_order_no="O2", direct_compensation=Decimal("8"), second_visit_fee=Decimal("2"),
    )
    assert aftersales_finance_service.total_cost(linked) == Decimal("30")
    assert aftersales_finance_service.total_cost(legacy) == Decimal("10")

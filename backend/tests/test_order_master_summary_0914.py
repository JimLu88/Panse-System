from datetime import date
from decimal import Decimal

import pytest

from app.models.order import Order, OrderDetail
from app.services import order_line_delivery_service as delivery
from app.services import taobao_order_import as importer


def row(sub, *, sku=None):
    return dict(sub_order_no=sub, product_name="组合柜,组合柜" if not sku else "组合柜",
                sku_code=sku, sku=sku, amount=Decimal("1200"), qty=1)


@pytest.mark.parametrize("master_first", [True, False])
def test_two_report_orders_do_not_create_two_deliverable_copies(db_session, master_first):
    o = Order(order_no="PARENT", platform="淘宝", order_date=date(2026, 9, 13),
              status="paid", paid_amount=Decimal("2400"))
    db_session.add(o)
    db_session.flush()
    from app.services import taobao_listing_service
    resolver = taobao_listing_service.build_resolver(db_session)
    master, children = [row("PARENT")], [row("CHILD-1", sku="PPS2415003051314"),
                                        row("CHILD-2", sku="PPS2415003051320")]
    for batch in ([master, children] if master_first else [children, master]):
        importer._persist_order_lines(db_session, o.order_no, batch, resolver,
                                      enable_factory_delivery=True)
        db_session.flush()
    all_rows = db_session.query(OrderDetail).all()
    eligible = [r.sub_order_no for r in all_rows if delivery.line_is_factory_eligible(db_session, r, o)]
    assert sorted(eligible) == ["CHILD-1", "CHILD-2"]
    assert len(all_rows) == (3 if master_first else 2)


def test_legacy_single_row_without_distinct_children_remains_eligible(db_session):
    o = Order(order_no="SINGLE", platform="淘宝", order_date=date(2026, 9, 13),
              status="paid", paid_amount=Decimal("1200"))
    r = OrderDetail(order_no="SINGLE", sub_order_no="SINGLE", source="import",
                    product_name="柜子", factory_delivery_required=True)
    db_session.add_all([o, r]); db_session.flush()
    assert delivery.line_is_factory_eligible(db_session, r, o)


def test_historical_summary_receipt_is_preserved_but_not_eligible(db_session):
    o = Order(order_no="P", platform="淘宝", status="paid", paid_amount=Decimal("2400"))
    r = OrderDetail(order_no="P", sub_order_no="P", source="import",
                    factory_no=484, factory_delivery_state="sent",
                    factory_delivery_message_id="existing-receipt", factory_delivery_required=True)
    child = OrderDetail(order_no="P", sub_order_no="C", source="import", sku_code="SKU")
    db_session.add_all([o, r, child]); db_session.flush()
    assert not delivery.line_is_factory_eligible(db_session, r, o)
    assert r.factory_no == 484 and r.factory_delivery_state == "sent"
    assert r.factory_delivery_message_id == "existing-receipt"


def test_sent_summary_requires_review_without_automatic_void(db_session, monkeypatch):
    o = Order(order_no="P", platform="淘宝", status="paid", paid_amount=Decimal("2400"))
    summary = OrderDetail(order_no="P", sub_order_no="P", source="import", factory_no=484,
                          factory_delivery_state="sent", factory_delivery_required=True)
    child = OrderDetail(order_no="P", sub_order_no="C", source="import", sku_code="SKU",
                        factory_delivery_required=True)
    db_session.add_all([o, summary, child]); db_session.flush()
    from app.models.import_file import ImportedFile
    monkeypatch.setattr(delivery, "sent_line_evidence", lambda db: {
        key: ImportedFile(id=i, kind='order_sheet_sent', stored_path='unused', row_summary={})
        for i, key in enumerate(('P', 'C'), 1)})
    monkeypatch.setattr(delivery, "void_line_evidence", lambda db: {})
    gate = delivery.delivery_count_gate(db_session)
    assert not gate["ok"]
    assert gate["master_summary_sent_sub_order_nos"] == ["P"]
    assert gate["missing_sub_order_nos"] == []
    assert summary.factory_delivery_state == "sent" and summary.factory_no == 484


@pytest.mark.parametrize("parent_count,child_count", [(0, 17), (2, 3), (0, 0)])
def test_closeout_counts_parent_and_child_receipts(db_session, monkeypatch, parent_count, child_count):
    from app.services import order_delivery_completion_service as closeout
    from app.services import order_sheet_archive_service, factory_dispatch_feishu_service
    from app.services import automation_pipeline_service
    monkeypatch.setattr(order_sheet_archive_service, "reconcile_pending_delivery",
                        lambda *a, **k: {"images_pushed": parent_count, "line_images_pushed": child_count})
    monkeypatch.setattr(factory_dispatch_feishu_service, "sync_if_enabled",
                        lambda *a, **k: {"ok": True})
    result = closeout.complete_recovered_order_delivery(db_session, source="test", manifest=["fixture.xlsx"])
    assert result.get("_run_status") is None
    state = automation_pipeline_service.get_pipeline(db_session, "order_delivery")
    assert f"下单图送达{parent_count + child_count}张" in state["last_stage"]["detail"]
    assert f"子单{child_count}张" in state["last_stage"]["detail"]

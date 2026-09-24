"""One old single-line order may be recovered without deleting its baseline."""
from datetime import date
from decimal import Decimal

from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail
from app.services import order_sheet_archive_service as sheets


ORDER_NO = "3306387146811014595"
SUB_NO = "3306387146811014595"


def _fixture(db, *, sibling=False, state=None, pushed=False, remote=False):
    order = Order(
        platform="淘宝", order_no=ORDER_NO, order_date=date(2026, 6, 7),
        status="paid", paid_amount=Decimal("2824.50"), refund_amount=Decimal("0"),
        product_name="实木床", sku="标准", sku_code="PPS2421007090114", qty=1,
        customer_address="上海市松江区测试路1号", is_refill=False,
        seller_memo="等通知发货" if remote else "正常排产",
    )
    line = OrderDetail(
        sync_key=f"line:{SUB_NO}", order_no=ORDER_NO, sub_order_no=SUB_NO,
        source="import", line_status="paid", sku_code="PPS2421007090114",
        sku_name="标准", product_name="实木床", qty=1,
        factory_delivery_required=False, factory_delivery_state=state,
    )
    old = ImportedFile(
        kind="order_sheet", original_filename=f"2026-06-07_{ORDER_NO}.jpg",
        stored_path="/preserved/old-sheet.jpg", source="baseline",
        row_summary={"pushed": pushed, "baseline": True},
    )
    db.add_all([order, line, old])
    if sibling:
        db.add(OrderDetail(
            sync_key="line:sibling", order_no=ORDER_NO, sub_order_no="sibling",
            source="import", line_status="paid", sku_code="SECOND", qty=1,
        ))
    db.flush()
    return order, line, old


def test_dry_run_leaves_legacy_evidence_and_line_unchanged(db_session):
    order, line, old = _fixture(db_session)
    result = sheets.deliver_unsent_legacy_single_line(
        db_session, order_no=ORDER_NO, sub_order_no=SUB_NO,
    )
    assert result["ok"] and result["dry_run"]
    assert result["old_sheet_ids_preserved"] == [old.id]
    assert not line.factory_delivery_required and line.factory_no is None
    assert order.factory_no is None and db_session.get(ImportedFile, old.id) is old


def test_commit_uses_scoped_child_sender_and_preserves_old_sheet(db_session, monkeypatch):
    order, line, old = _fixture(db_session)
    calls = []

    def send_one(db, *, limit, only_sub_order_nos):
        calls.append((limit, only_sub_order_nos))
        assert line.factory_delivery_required
        line.factory_no = 600
        line.factory_delivery_state = "sent"
        line.factory_delivery_message_id = "om_legacy_one"
        db.commit()
        return {"pushed": 1, "failed": 0, "order_nos": [ORDER_NO]}

    monkeypatch.setattr(sheets, "reconcile_order_line_delivery", send_one)
    result = sheets.deliver_unsent_legacy_single_line(
        db_session, order_no=ORDER_NO, sub_order_no=SUB_NO, dry_run=False,
    )
    assert result["ok"] and result["message_id"] == "om_legacy_one"
    assert result["factory_no"] == 600 and calls == [(1, {SUB_NO})]
    assert db_session.get(ImportedFile, old.id) is not None
    assert order.factory_no is None
    repeat = sheets.deliver_unsent_legacy_single_line(
        db_session, order_no=ORDER_NO, sub_order_no=SUB_NO, dry_run=False,
    )
    assert not repeat["ok"] and len(calls) == 1


def test_refuses_sibling_or_existing_delivery(db_session):
    _, _, old = _fixture(db_session, sibling=True)
    result = sheets.deliver_unsent_legacy_single_line(
        db_session, order_no=ORDER_NO, sub_order_no=SUB_NO, dry_run=False,
    )
    assert not result["ok"] and "唯一" in result["error"]
    assert db_session.get(ImportedFile, old.id) is not None


def test_refuses_unknown_delivery_state(db_session):
    _, line, _ = _fixture(db_session, state="uncertain")
    result = sheets.deliver_unsent_legacy_single_line(
        db_session, order_no=ORDER_NO, sub_order_no=SUB_NO, dry_run=False,
    )
    assert not result["ok"] and not line.factory_delivery_required


def test_refuses_already_pushed_archive(db_session):
    _, line, _ = _fixture(db_session, pushed=True)
    result = sheets.deliver_unsent_legacy_single_line(
        db_session, order_no=ORDER_NO, sub_order_no=SUB_NO, dry_run=False,
    )
    assert not result["ok"] and not line.factory_delivery_required


def test_refuses_still_remote(db_session):
    _, line, _ = _fixture(db_session, remote=True)
    result = sheets.deliver_unsent_legacy_single_line(
        db_session, order_no=ORDER_NO, sub_order_no=SUB_NO, dry_run=False,
    )
    assert not result["ok"] and not line.factory_delivery_required

from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.models.order import OrderDetail
from app.services import taobao_order_import as imp
from app.services import order_line_delivery_service as lines
from app.services import factory_dispatch_feishu_service as dispatch
from app.services import scheduler, agent_ingest_service as ingest
from tests.test_factory_dispatch_field_contract import seed, mock_remote
from tests.test_order_line_factory_delivery_0812 import _feishu, _order, _line


def master_bytes():
    wb = Workbook()
    ws = wb.active
    ws.title = "export"
    ws.append(["订单编号", "买家应付货款", "买家实付金额", "订单状态",
               "收货地址", "订单创建时间", "商品标题", "物流公司", "宝贝总数量",
               "退款金额", "商品属性SKU", "商家编码"])
    ws.append(["MAIN", 4335, 4330, "买家已付款,等待卖家发货", "测试市测试路1号",
               "2026-11-18 12:00:00", "餐桌", "", 3, 5,
               "尺寸定制（联系客服）", "PPS2421007090199"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_real_master_is_not_address_only(db_session):
    raw = master_bytes()
    assert imp.detect_report_role("ExportOrderList.xlsx", raw) == "orders"
    rep = imp.import_taobao_orders(db_session, "ExportOrderList.xlsx", raw)
    assert not rep.errors
    from app.models.order import Order
    order = db_session.query(Order).one()
    assert not (order.platform_field_state or {}).get("shipping_only")
    assert order.qty == 3


@pytest.mark.parametrize("status,refund_status,amount,refund,blocked", [
    ("paid", "退款成功", 4335, 5, False),
    ("production", "退款完成", 4335, 5, False),
    ("signed", "退款成功", 4335, 5, False),
    ("cancelled", "退款成功", 4335, 5, True),
    ("aftersales", "退款成功", 4335, 5, True),
    ("paid", "退款成功", 4335, 4335, True),
    ("paid", "退款成功", 0, 5, True),
    ("paid", "退款处理中", 4335, 5, True),
    ("paid", "退货退款成功", 4335, 5, True),
    ("paid", "没有申请退款", 4335, 0, False),
])
def test_completed_partial_refund_is_not_product_cancellation(
        status, refund_status, amount, refund, blocked):
    line = OrderDetail(line_status=status, refund_status=refund_status,
                       amount=Decimal(amount), refund_amount=Decimal(refund))
    assert lines.line_is_refunded(line) is blocked


def test_scoped_table_sync_preserves_other_orders_and_verifies_repeat(db_session, monkeypatch):
    seed(db_session, ("paid",))
    unrelated = {"record_id": "other", "fields": {
        "订单号": "OTHER", "子订单号": "OTHER-LINE", "工厂下单号": "保留", "工厂手填": "手工"}}
    records = [unrelated]
    calls = mock_remote(monkeypatch, records)
    result = dispatch.sync(db_session, include_images=False, only_order_nos={"TEST-PARENT"})
    assert result["ok"] and result["created"] == 1
    assert result["verified_entity_count"] == 1
    assert unrelated["fields"]["工厂手填"] == "手工"
    second = dispatch.sync(db_session, include_images=False, only_order_nos={"TEST-PARENT"})
    assert second["ok"] and second["created"] == second["updated"] == 0
    assert len(records) == 2


def test_hourly_delivery_also_syncs_table_when_one_image_fails(db_session, monkeypatch):
    monkeypatch.setattr(ingest, "order_data_fresh", lambda *a, **k: True)
    monkeypatch.setattr(scheduler, "_sync_factory_dispatch_after_orders",
                        lambda db, result: result.update(factory_dispatch={"ok": True}))
    from app.services import order_sheet_archive_service as sheets
    monkeypatch.setattr(sheets, "reconcile_pending_delivery",
                        lambda *a, **k: {"_run_status": "fail", "images_pushed": 1})
    result = scheduler._job_order_sheets_catchup(db_session)
    assert result["factory_dispatch"]["ok"]
    assert result["_run_status"] == "fail"


def test_repair_wrong_role_reimports_once(db_session, monkeypatch, tmp_path):
    from app.models.import_file import ImportedFile
    from app.services import import_storage
    import hashlib
    raw = master_bytes()
    path = tmp_path / "2026-11-18" / "taobao" / "ExportOrderList.xlsx"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    monkeypatch.setattr(ingest, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(import_storage, "get_root", lambda: tmp_path / "archive")
    db_session.add(ImportedFile(kind="taobao", original_filename=path.name,
        stored_path=str(path), file_hash=hashlib.sha256(raw).hexdigest(),
        source="api", row_summary={"agent_status": "imported", "agent_report_role": "shipping"}))
    db_session.commit()
    kwargs = dict(only_paths=[str(path)], artifact_roles={path.name: "orders"},
                  order_batch_id="orders-20261118-test")
    first = ingest.run_ingest(db_session, **kwargs)
    assert first["imported"] == 1 and first["errors"] == 0
    second = ingest.run_ingest(db_session, **kwargs)
    assert second["imported"] == 0 and second["skipped_known"] == 1


def test_confirmed_partial_refund_delivers_one_and_retries_table_without_resending(
        db_session, monkeypatch, _feishu):
    from app.services import settings_service, factory_production_evidence as facts
    from app.services import factory_sheet, order_sheet_archive_service as sheets
    from app.services.order_delivery_completion_service import complete_selected_order_delivery
    settings_service.set_value(db_session, "feishu_push_chat_id", "test")
    order = _order(db_session, "TEST-PARTIAL", name="岩板餐桌")
    line = _line(db_session, order.order_no, order.order_no, "岩板餐桌", "PPS2421007090199")
    line.qty, line.amount, line.refund_amount = 3, Decimal("4335"), Decimal("5")
    line.refund_status, line.sku_name, line.factory_delivery_required = "退款成功", "尺寸定制（联系客服）", True
    order.qty, order.buyer_message = 3, "2.2*0.9定制"
    db_session.commit()
    facts.confirm_quantity(db_session, line_id=line.id, expected_sku=line.sku_code,
                           expected_purchase_qty=3, physical_qty=1, actor="operator", evidence_ref="explicit")
    sheet = factory_sheet.build_for_order_line(db_session, order.id, line.id)
    assert sheet.qty == 1 and sheet.purchase_qty == 3
    results = iter([{"ok": False, "errors": ["table unavailable"]}, {"ok": True}])
    monkeypatch.setattr(dispatch, "sync", lambda *a, **k: next(results))
    first = complete_selected_order_delivery(db_session, sub_order_nos=[line.sub_order_no])
    assert not first["ok"] and len(first["receipts"]) == 1
    second = complete_selected_order_delivery(db_session, sub_order_nos=[line.sub_order_no])
    assert second["ok"] and second["delivery"]["pushed"] == 0
    assert _feishu == ["img-key"]
    assert sheets.reconcile_refunded_order_lines(db_session)["voided"] == 0
    assert line.qty == 3 and line.refund_amount == Decimal("5")

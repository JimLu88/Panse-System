from datetime import date
from io import BytesIO
import json

from openpyxl import Workbook
import pytest

from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.services import import_storage, order_post_push_audit as audit
from app.services import factory_dispatch_feishu_service as table
from app.services import factory_production_evidence as facts
from tests.test_factory_dispatch_field_contract import mock_remote
from tests.test_order_line_factory_delivery_0812 import _feishu, _order, _line

PARENT = "5127440187078011135"
SKU = "PPS2633007032018"


def xlsx(title, header, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(header)
    for row in rows:
        ws.append(row)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


@pytest.fixture
def exported(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(import_storage, "get_root", lambda: tmp_path)
    def create(items=None):
        items = items or [(PARENT, PARENT, SKU, 1, "买家已付款,等待卖家发货", "没有申请退款", 0)]
        batch = "orders-" + date.today().strftime("%Y%m%d") + "-test"
        master = xlsx("export",
            ["订单编号", "订单创建时间", "订单状态", "买家实付金额", "商品标题",
             "宝贝总数量", "商家编码", "商品属性SKU", "收货地址", "物流公司"],
            [[p, str(date.today())+" 18:00:00", status, 5000, "床", qty, sku, "床",
              "测试市测试路1号", ""] for p,sub,sku,qty,status,refund,amount in items])
        detail = xlsx("export",
            ["主订单编号", "子订单编号", "商家编码", "购买数量", "商品标题",
             "商品属性", "订单状态", "买家应付货款", "退款状态", "退款金额"],
            [[p,sub,sku,qty,"床","床",status,5000,refund,amount]
             for p,sub,sku,qty,status,refund,amount in items])
        names, roles, files = [], {}, {}
        for role, raw in [("orders", master), ("sales_detail", detail), ("shipping", b"encrypted-shipping")]:
            name = role+".xlsx"
            file = import_storage.archive(db_session, content=raw, original_name=name,
                kind="taobao", source="api", row_summary={"agent_status": "imported",
                "agent_report_role": role, "automation_batch_id": batch}).file
            names.append(name); roles[name] = role; files[role] = file
        db_session.add(SystemSetting(key="order_pull_completion_"+batch, is_secret=False,
            value_plain=json.dumps({"order_batch_id": batch, "order_business_date": str(date.today()),
                                    "artifacts": names, "artifact_roles": roles})))
        db_session.commit()
        return batch, files
    return create


@pytest.fixture
def remote(monkeypatch):
    records = []
    calls = mock_remote(monkeypatch, records)
    monkeypatch.setattr(table, "_attachment_value",
                        lambda db, app, path, signature, name, cache: [{"file_token": signature}])
    return records, calls


def existing(db):
    from app.services import settings_service
    settings_service.set_value(db, "feishu_push_chat_id", "test")
    order = _order(db, PARENT)
    line = _line(db, PARENT, PARENT, "床", SKU)
    line.factory_delivery_required = True
    db.commit()
    return order, line


@pytest.mark.parametrize("audit_ok", [True, False])
def test_daily_notice_waits_for_audit_and_counts_line_images(db_session, monkeypatch, audit_ok):
    from app.services import order_sheet_archive_service as sheets, notify_service, scheduler
    events = []
    monkeypatch.setattr(sheets, "reconcile_pending_delivery", lambda *a, **k: {
        "images_pushed": 0, "line_images_pushed": 2, "images_remaining": 0,
        "order_nos": [PARENT]})
    monkeypatch.setattr(table, "sync_if_enabled", lambda db: {"ok": True})
    def checked(*a, **k):
        events.append("audit")
        return {"ok": audit_ok, "images_pushed": 1, "remaining_count": 0 if audit_ok else 1}
    monkeypatch.setattr(audit, "audit", checked)
    def notify(db, text, **kwargs):
        assert events == ["audit"]
        events.append("notify")
        assert kwargs["level"] == ("info" if audit_ok else "warn")
        assert ("今日推送 3 张" in text) if audit_ok else ("核对未完成" in text)
        return True, "ok"
    monkeypatch.setattr(notify_service, "notify", notify)
    result = sheets.push_daily(db_session)
    scheduler._sync_factory_dispatch_after_orders(db_session, result)
    assert events == ["audit", "notify"]


def test_missing_image_and_table_are_repaired_once_from_export(db_session, exported, remote, _feishu):
    batch, _ = exported()
    existing(db_session)
    first = audit.audit(db_session, batch_id=batch)
    assert first["ok"], first
    assert first["images_pushed"] == first["table_created"] == first["verified_count"] == 1
    second = audit.audit(db_session, batch_id=batch)
    assert second["ok"] and second["images_pushed"] == second["table_created"] == 0
    assert len(_feishu) == 1 and len(remote[0]) == 1


def test_low_amount_product_is_visible_then_explicitly_released(db_session, exported, remote, _feishu):
    from decimal import Decimal
    batch, _ = exported()
    order, line = existing(db_session)
    order.paid_amount = Decimal('285.52')
    db_session.commit()
    held = audit.audit(db_session, batch_id=batch)
    assert not held['ok']
    assert held['remaining'] == [{'order_no': PARENT, 'sub_order_no': PARENT,
        'reason': 'low_value_order_needs_review', 'detail': '金额¥286 < 门槛¥400'}]
    assert not _feishu
    facts.confirm_low_value_physical_order(db_session, order_no=PARENT,
        expected_sku=SKU, expected_qty=1, expected_amount='285.52',
        actor='explicit-user', evidence_ref='request:low-value-item')
    released = audit.audit(db_session, batch_id=batch)
    assert released['ok'] and released['images_pushed'] == released['verified_count'] == 1
    assert audit.audit(db_session, batch_id=batch)['images_pushed'] == 0
    assert len(_feishu) == 1 and order.paid_amount == Decimal('285.52')


def test_deleted_table_row_is_recreated_without_resending_group_image(db_session, exported, remote, _feishu):
    batch, _ = exported()
    existing(db_session)
    assert audit.audit(db_session, batch_id=batch)["ok"]
    remote[0].clear()
    result = audit.audit(db_session, batch_id=batch)
    assert result["ok"] and result["table_created"] == 1 and result["images_pushed"] == 0
    assert len(_feishu) == 1


def test_missing_erp_child_repaired_from_source_not_erp_projection(db_session, exported, remote, _feishu):
    batch, _ = exported()
    from app.services import settings_service
    settings_service.set_value(db_session, "feishu_push_chat_id", "test")
    _order(db_session, PARENT)
    db_session.commit()
    result = audit.audit(db_session, batch_id=batch)
    assert result["ok"], result
    assert len(result["erp_repaired"]) == 1 and len(_feishu) == 1
    assert db_session.query(OrderDetail).filter_by(sub_order_no=PARENT).one().qty == 1


def test_changed_quantity_is_reported_never_overwritten_or_sent(db_session, exported, remote, _feishu):
    batch, _ = exported()
    _, line = existing(db_session)
    line.qty = 2; db_session.commit()
    result = audit.audit(db_session, batch_id=batch)
    assert not result["ok"]
    assert result["remaining"][0]["reason"] == "source_erp_conflict"
    assert line.qty == 2 and not _feishu


def test_unknown_send_is_not_replayed(db_session, exported, remote, _feishu):
    batch, _ = exported()
    _, line = existing(db_session)
    line.factory_delivery_state = "uncertain"; db_session.commit()
    result = audit.audit(db_session, batch_id=batch)
    assert not result["ok"] and not _feishu
    assert any(x.get("reason") == "delivery_uncertain_no_replay" for x in result["remaining"])


def test_tampered_source_blocks_every_write(db_session, exported, remote, _feishu):
    batch, files = exported()
    existing(db_session)
    files["sales_detail"].file_hash = "incorrect"; db_session.commit()
    result = audit.audit(db_session, batch_id=batch)
    assert not result["ok"] and "hash_mismatch" in result["reason"]
    assert not _feishu and not remote[1]


def test_closed_source_excluded_without_production(db_session, exported, remote, _feishu):
    batch, _ = exported([(PARENT, PARENT, SKU, 1, "交易关闭", "退款成功", 5000)])
    result = audit.audit(db_session, batch_id=batch)
    assert result["ok"] and result["source_product_count"] == 0
    assert len(result["excluded"]) == 1 and not _feishu and not remote[0]


def test_confirmed_physical_quantity_not_pricing_units(db_session, exported, remote, _feishu):
    batch, _ = exported([(PARENT, PARENT, SKU, 3, "买家已付款,等待卖家发货", "没有申请退款", 0)])
    order, line = existing(db_session)
    line.qty = order.qty = 3; line.sku_name = "尺寸定制（联系客服）"
    db_session.commit()
    facts.confirm_quantity(db_session,line_id=line.id,expected_sku=SKU,
        expected_purchase_qty=3,physical_qty=1,actor="user",evidence_ref="explicit")
    result = audit.audit(db_session, batch_id=batch)
    assert result["ok"], result
    assert remote[0][0]["fields"]["订购数量"] == 1
    assert line.qty == 3 and len(_feishu) == 1


def test_readback_failure_not_false_success(db_session, exported, remote, _feishu, monkeypatch):
    batch, _ = exported()
    existing(db_session)
    monkeypatch.setattr(table.feishu_client, "batch_create_records", lambda *a: ["pretend-success"])
    result = audit.audit(db_session, batch_id=batch)
    assert not result["ok"] and result["images_pushed"] == 1
    assert result["remaining_count"] > 0


def test_no_export_cannot_claim_reconciliation(db_session, remote, _feishu):
    result = audit.audit(db_session)
    assert not result["ok"] and result["reason"] == "missing_completed_export_batch"
    assert not _feishu and not remote[0]


def test_missing_entire_erp_order_is_imported_but_incomplete_address_stays_held(
        db_session, exported, remote, _feishu):
    batch, _ = exported()
    result = audit.audit(db_session, batch_id=batch)
    assert db_session.query(Order).filter_by(order_no=PARENT).one()
    assert db_session.query(OrderDetail).filter_by(sub_order_no=PARENT).one()
    assert len(result["erp_repaired"]) == 1
    # No phone/source customer proof: it must not fabricate a production address.
    assert not _feishu and not result["ok"]


def test_generic_custom_order_without_sku_is_a_visible_gap(
        db_session, exported, remote, _feishu):
    batch, _ = exported([(PARENT, PARENT, "", 9, "买家已付款,等待卖家发货", "没有申请退款", 0)])
    result = audit.audit(db_session, batch_id=batch)
    assert not result["ok"]
    assert result["remaining"][0]["reason"] == "source_sku_or_quantity_missing"
    assert not _feishu


def test_unknown_source_status_not_silently_excluded(db_session, exported, remote, _feishu):
    batch, _ = exported([(PARENT, PARENT, SKU, 1, "未知状态", "没有申请退款", 0)])
    result = audit.audit(db_session, batch_id=batch)
    assert not result["ok"] and result["remaining"][0]["reason"] == "source_status_unknown"


def test_missing_whole_detail_order_cannot_pass(db_session, exported, remote, _feishu, monkeypatch):
    batch, _ = exported()
    original = audit._sources
    def missing(*args):
        batch, masters, details, files = original(*args)
        return batch, masters, {}, files
    monkeypatch.setattr(audit, "_sources", missing)
    result = audit.audit(db_session, batch_id=batch)
    assert not result["ok"]
    assert result["remaining"][0]["reason"] == "sales_detail_missing_from_export"
    assert not _feishu


def test_explicit_child_scope_does_not_send_sibling(db_session, exported, remote, _feishu):
    other = "5127440187078022222"
    batch, _ = exported([
        (PARENT, PARENT, SKU, 1, "买家已付款,等待卖家发货", "没有申请退款", 0),
        (PARENT, other, SKU, 1, "买家已付款,等待卖家发货", "没有申请退款", 0)])
    existing(db_session)
    second = _line(db_session, PARENT, other, "床", SKU)
    second.factory_delivery_required = True
    db_session.commit()
    result = audit.audit(db_session, batch_id=batch, only_sub_order_nos={PARENT})
    assert result["ok"], result
    assert len(_feishu) == 1 and not second.factory_delivery_message_id


def test_readonly_audit_does_not_repair(db_session, exported, remote, _feishu):
    batch, _ = exported()
    existing(db_session)
    before = db_session.query(SystemSetting).count()
    result = audit.audit(db_session, batch_id=batch, repair=False)
    assert not result["ok"] and not _feishu and not remote[0]
    assert db_session.query(SystemSetting).count() == before

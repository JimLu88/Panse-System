from datetime import date
import hashlib
from types import SimpleNamespace
import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.order import Order, OrderDetail
from app.models.import_file import ImportedFile
from app.models.fulfillment_intake import FulfillmentIntake
from app.services import fulfillment_deadlines as deadlines, fulfillment_intake as intake, settings_service
from app.services.feishu_bot_service import on_message_event

START = date(2026, 11, 1)
MID = "om_synthetic"
TID = "9999999999999991"
SUB = "9999999999999992"
TEXT = "发货登记 整单单包已发 德邦快递 DP12345678"


@pytest.mark.parametrize("text,due,action,reason", [
    ("下单后十天内完成", "2026-11-11", "完成制作", "约定"),
    ("下单后3天内发货", "2026-11-04", "发货", "约定"),
    ("下单后十五天内完成", "2026-11-16", "完成制作", "约定"),
    ("付款后7天内发货", "2026-12-01", "核实交期", "起算"),
    ("付款后7天发货", "2026-12-01", "核实交期", "起算"),
    ("7天内完成", "2026-12-01", "核实交期", "起算"),
    ("20天内", "2026-12-01", "核实交期", "完成事项"),
    ("工期约二十天", "2026-12-01", "核实交期", "起点"),
    ("下单后三天内送达", "2026-12-01", "核实交期", "运输"),
    ("下单后3天内发货可以吗？", "2026-12-01", "核实交期", "核实"),
    ("下单后3天内发货，下单后8天内发货", "2026-12-01", "核实交期", "不同"),
])
def test_deadline_distinctions(text, due, action, reason):
    result = deadlines.deadline(SimpleNamespace(remark=text, order_date=START), START)
    assert result["due"] == due and result["action"] == action and reason in result["reason"]


@pytest.mark.parametrize("text", ["暂不发货，下单后三天内完成", "10天后再发货", "无需加急"])
def test_holds_and_negation(text):
    assert deadlines.deadline(SimpleNamespace(remark=text, order_date=START), START) is None


def test_normal_near_deadline_and_manual_precedence():
    item = SimpleNamespace(remark="", order_date=START)
    assert deadlines.deadline(item, date(2026, 11, 25))["days"] == 6
    item.remark, item.ship_deadline = "下单后3天内发货", date(2026, 11, 9)
    assert deadlines.deadline(item, START)["due"] == "2026-11-09"


def setup(db):
    order = Order(platform="淘宝", order_no=TID, qty=1, status="paid", shop="test", order_date=START)
    line = OrderDetail(order_no=TID, sub_order_no=SUB, source="import", qty=1, factory_no=42,
        line_status="paid", sku_code="s", product_code="p", factory_delivery_required=True)
    db.add_all([order, line])
    db.flush()
    digest = hashlib.sha256(b"original").hexdigest()
    evidence = ImportedFile(kind="order_sheet_sent", stored_path="test", file_hash=digest,
        row_summary={"order_no": TID, "sub_order_no": SUB, "line_id": line.id, "pushed": True,
                     "factory_no_at_render": 42, "rendered_line": {"schema": "factory-line-v2",
                     "content_sha256": digest, "qty": 1, "sku_code": "s", "product_code": "p"}})
    db.add(evidence)
    settings_service.set_value(db, "feishu_shipping_intake_chat_id", "test-chat")
    settings_service.set_value(db, "feishu_shipping_operator_ids", "test-op")
    db.commit()
    return order, line


def event(mid=MID):
    import json
    return {"_factory_forward_trusted": True, "sender": {"sender_type": "user",
            "sender_id": {"open_id": "test-op"}}, "message": {"chat_id": "test-chat",
            "message_id": mid, "message_type": "post", "content": json.dumps({"content": [[
            {"tag": "text", "text": TEXT}, {"tag": "img", "image_key": "test-image"}]]})}}


class Client:
    shop = "test"
    sends = 0
    verified = False

    def exact_trade(self, row, status):
        return True

    def carrier_code(self, carrier):
        return "TEST"

    def send(self, row, code):
        self.sends += 1
        raise TimeoutError()

    def confirmed(self, row):
        return self.verified


def test_lost_ack_replay_only_reads(db_session, monkeypatch):
    db = db_session
    setup(db)
    client = Client()
    monkeypatch.setattr(intake, "TaobaoShipping", lambda: client)
    monkeypatch.setenv("PANSE_TOP_SHIPPING_ENABLED", "1")
    monkeypatch.setattr(intake.feishu_client, "download_message_resource", lambda *args: b"original")
    monkeypatch.setattr("app.services.feishu_bot_service._safe_reply", lambda *a: "om_reply")
    assert on_message_event(db, event())["state"] == "unknown"
    assert on_message_event(db, event())["duplicate"]
    assert on_message_event(db, event("om_b"))["state"] == "review"
    assert client.sends == 1
    client.verified = True
    assert intake.reconcile_pending(db)["checked"] == 1
    assert db.get(FulfillmentIntake, MID).state == "confirmed"
    assert client.sends == 1 and db.scalar(select(Order)).status == "paid"


@pytest.mark.parametrize("reason", ["untrusted", "other_chat", "other_user", "refunded", "compressed", "photo", "multi", "disabled"])
def test_gate_prevents_submission(reason, db_session, monkeypatch):
    db = db_session
    order, line = setup(db)
    incoming = event()
    client = Client()
    monkeypatch.setattr(intake, "TaobaoShipping", lambda: client)
    monkeypatch.setenv("PANSE_TOP_SHIPPING_ENABLED", "0" if reason == "disabled" else "1")
    monkeypatch.setattr(intake.feishu_client, "download_message_resource", lambda *args: b"small" if reason == "compressed" else b"original")
    monkeypatch.setattr("app.services.vision_ocr_service.read_factory_shipping_identity",
                        lambda *args: {"sub_order_no": SUB, "factory_no": 42})
    if reason == "untrusted":
        incoming["_factory_forward_trusted"] = False
    elif reason == "other_chat":
        incoming["message"]["chat_id"] = "other"
    elif reason == "other_user":
        incoming["sender"]["sender_id"]["open_id"] = "other"
    elif reason == "refunded":
        line.refund_status = "退款成功"
    elif reason == "photo":
        order.remark = "发出前必须岩板拍照"
    elif reason == "multi":
        db.add(OrderDetail(order_no=TID, sub_order_no="other", source="import"))
    db.commit()
    assert intake.register(db, incoming, TEXT, ["image"])["state"] in ("unbound", "unauthorized", "review")
    assert client.sends == 0


def test_official_readback_requires_matching_seller_item_and_waybill(monkeypatch):
    from app.services.taobao_shipping import TaobaoShipping
    client = object.__new__(TaobaoShipping)
    client.seller = "测试店"
    row = SimpleNamespace(order_no=TID, sub_order_no=SUB, tracking_no="DP12345678", carrier="德邦快递")
    line = {"oid": SUB, "num": 1, "refund_status": "NO_REFUND", "status": "WAIT_BUYER_CONFIRM_GOODS"}
    trade = {"tid": TID, "seller_nick": client.seller, "status": line["status"], "orders": {"order": [line]}}
    parcel = {"tid": TID, "out_sid": row.tracking_no, "company_name": row.carrier, "seller_confirm": True}
    monkeypatch.setattr(client, "trade", lambda _: trade)
    monkeypatch.setattr(client, "call", lambda *a, **kw: {"shippings": {"shipping": [parcel]}})
    assert client.confirmed(row)
    for obj, key, value in [(trade, "seller_nick", "别家"), (line, "num", 2), (line, "oid", "other"),
                             (line, "refund_status", "WAIT_SELLER_AGREE"), (parcel, "company_name", "other"),
                             (parcel, "out_sid", "other"), (parcel, "seller_confirm", False)]:
        original = obj[key]
        obj[key] = value
        assert not client.confirmed(row)
        obj[key] = original


def test_internal_reminder_groups_and_resolves(db_session, monkeypatch):
    db = db_session
    order, line = setup(db)
    pending = deadlines.pending_orders
    monkeypatch.setattr(deadlines, "pending_orders", lambda db: pending(db, date(2026, 11, 30)))
    assert deadlines.refresh_reminder(db)["orders"] == 1
    assert deadlines.refresh_reminder(db)["orders"] == 1
    assert len(list(db.scalars(select(Alert)))) == 1
    line.line_status = "shipped"
    db.commit()
    assert deadlines.refresh_reminder(db)["orders"] == 0
    assert db.scalar(select(Alert)).resolved_at is not None

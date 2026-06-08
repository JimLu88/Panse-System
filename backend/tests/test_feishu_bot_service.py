# -*- coding: utf-8 -*-
"""飞书机器人(发图→识别→卡片→入库) 可测部分单测 (不依赖真飞书/真AI)。"""
import json

from app.models.order import Order
from app.services import feishu_bot_service as B
from app.services import feishu_client, vision_ocr_service
from app.services.ai_provider import AiUnavailable


def test_picker_and_confirm_cards():
    pick = B._picker_card("m1")
    assert pick["header"]["title"]["content"].startswith("📷")
    ops = [a["value"]["kind"] for a in pick["elements"][1]["actions"] if a["value"].get("kind")]
    assert ops == ["order_table", "order_image", "supplier_note", "alipay_flow"]
    conf = B._confirm_card("m1", "supplier_note", 0.9)
    assert "供应商送货单" in conf["elements"][0]["text"]["content"]


def test_staging_roundtrip(db_session):
    B._stage(db_session, "m1", {"file_key": "k1", "kind": "order_table", "conf": 0.9})
    assert B._load_pending(db_session)["m1"]["file_key"] == "k1"


def test_classify_unknown_when_ai_unconfigured(db_session, monkeypatch):
    def _boom(_cfg):
        raise AiUnavailable("no key")
    monkeypatch.setattr("app.services.ai_provider.build_provider", _boom)
    assert B.classify_image(db_session, b"img") == ("unknown", 0.0)


def test_import_orders(db_session):
    parsed = {"orders": [
        {"order_no": "F1", "product_name": "畔色餐桌", "qty": 2, "paid_amount": "3999.00"},
        {"order_no": "F1"},          # 重复 → 跳过
        {"order_no": ""},            # 空号 → 忽略
    ], "ocr_warnings": ["第3行金额模糊"]}
    r = B._import_orders(db_session, parsed)
    assert r["inserted"] == 1 and r["skipped"] == 1
    o = db_session.query(Order).filter_by(order_no="F1").one()
    assert o.qty == 2 and str(o.paid_amount) == "3999.00"


def test_on_message_event_image_flow(db_session, monkeypatch):
    sent = {}
    monkeypatch.setattr(feishu_client, "download_message_resource", lambda *a, **k: b"IMG")
    monkeypatch.setattr(B, "classify_image", lambda *a, **k: ("order_table", 0.9))
    monkeypatch.setattr(feishu_client, "reply_card", lambda db, mid, card: sent.update(mid=mid, card=card))

    event = {"message": {"message_type": "image", "message_id": "m9",
                         "content": json.dumps({"image_key": "k9"})}}
    r = B.on_message_event(db_session, event)
    assert r["kind"] == "order_table" and r["card_sent"]
    assert sent["card"]["header"]["template"] == "green"        # 高置信 → 确认卡片
    assert B._load_pending(db_session)["m9"]["file_key"] == "k9"  # 已暂存

    # 非图片消息 → 忽略
    assert B.on_message_event(db_session, {"message": {"message_type": "text"}}) is None


def test_on_card_action_pick_imports(db_session, monkeypatch):
    B._stage(db_session, "m1", {"file_key": "k1", "kind": "order_table", "conf": 0.9})
    monkeypatch.setattr(feishu_client, "download_message_resource", lambda *a, **k: b"IMG")
    monkeypatch.setattr(vision_ocr_service, "parse_qianniu_order",
                        lambda db, img, **k: {"orders": [{"order_no": "Z1", "qty": 1}], "ocr_warnings": []})
    monkeypatch.setattr(feishu_client, "reply_card", lambda *a, **k: None)

    event = {"action": {"value": {"op": "pick", "message_id": "m1", "kind": "order_table"}}}
    r = B.on_card_action(db_session, event)
    assert r["ok"] and r["kind"] == "order_table"
    assert db_session.query(Order).filter_by(order_no="Z1").count() == 1


def test_on_card_action_expired(db_session, monkeypatch):
    monkeypatch.setattr(feishu_client, "reply_card", lambda *a, **k: None)
    r = B.on_card_action(db_session, {"action": {"value": {"op": "pick", "message_id": "nope", "kind": "order_table"}}})
    assert r["error"] == "expired"

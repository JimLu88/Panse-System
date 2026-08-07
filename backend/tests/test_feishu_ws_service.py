# -*- coding: utf-8 -*-
"""飞书机器人长连接(WebSocket) 接入 可测部分单测 (不起真连接/真飞书/真AI)。"""
import importlib.util
import time

import pytest

from app.models.order import Order
from app.services import automation_failure_recorder_service as failure_recorder
from app.services import feishu_bot_service as B
from app.services import feishu_client, feishu_ws_service as W, vision_ocr_service

# _on_card 内部 import lark_oapi 构造卡片响应; 生产 docker 镜像装了, 本机为可选依赖。
# 缺时优雅跳过这两条(其余测试不依赖 lark)。
_skip_no_lark = pytest.mark.skipif(
    importlib.util.find_spec("lark_oapi") is None,
    reason="lark_oapi 未安装(docker 生产镜像有; 本机可选依赖)")


def test_parse_card_event_variants():
    ev = {"action": {"value": {"op": "pick", "message_id": "m1", "kind": "order_table"}},
          "context": {"open_message_id": "card9"}}
    assert W._parse_card_event(ev) == ("pick", {"op": "pick", "message_id": "m1", "kind": "order_table"}, "card9")
    # value 是字符串(部分客户端) 也能解析
    ev2 = {"action": {"value": '{"op":"cancel","message_id":"m2"}'}}
    op, value, card = W._parse_card_event(ev2)
    assert op == "cancel" and value["message_id"] == "m2" and card is None


def test_to_dict_passthrough():
    assert W._to_dict({"event": {"x": 1}}) == {"x": 1}
    assert W._to_dict({"message": {"y": 2}}) == {"message": {"y": 2}}


def test_password_worker_failure_is_recorded_and_replied(db_session, monkeypatch):
    replies = []
    monkeypatch.setattr(
        B,
        "_safe_reply",
        lambda db, message_id, card: replies.append((message_id, card)),
    )
    event = {
        "message": {
            "message_id": "password-msg-1",
            "message_type": "text",
            "content": '{"text":"@_user_1 密码REDACTED"}',
        },
    }

    result = W._handle_worker_failure(db_session, event, TypeError("legacy tasks"))

    assert result["recorded"]["created"] is True
    assert replies[0][0] == "password-msg-1"
    assert "REDACTED" not in str(replies[0][1])
    events = failure_recorder.list_failure_events(db_session)
    assert any(item["reason"].endswith("TypeError") for item in events["items"])


@_skip_no_lark
def test_on_card_pick_acks_and_schedules(monkeypatch):
    called = {}
    monkeypatch.setattr(B, "process_pick", lambda *a, **k: called.update(args=a))
    resp = W._on_card({"action": {"value": {"op": "pick", "message_id": "m1", "kind": "order_table"}},
                       "context": {"open_message_id": "c1"}})
    assert type(resp).__name__ == "P2CardActionTriggerResponse"   # 3秒内 ack
    time.sleep(0.05)                                              # 等后台线程
    assert called["args"] == ("m1", "order_table", "c1")


@_skip_no_lark
def test_on_card_cancel(monkeypatch):
    monkeypatch.setattr(W, "_patch", lambda *a, **k: None)
    resp = W._on_card({"action": {"value": {"op": "cancel", "message_id": "m1"}},
                       "context": {"open_message_id": "c1"}})
    assert type(resp).__name__ == "P2CardActionTriggerResponse"


def test_do_pick_imports_and_patches(db_session, monkeypatch):
    B._stage(db_session, "m1", {"file_key": "k1", "kind": "order_table", "conf": 0.9})
    monkeypatch.setattr(feishu_client, "download_message_resource", lambda *a, **k: b"IMG")
    monkeypatch.setattr(vision_ocr_service, "parse_qianniu_order",
                        lambda db, img, **k: {"orders": [{"order_no": "W1", "qty": 1}], "ocr_warnings": []})
    patched = {}
    monkeypatch.setattr(feishu_client, "patch_card", lambda db, mid, card: patched.update(mid=mid, card=card))

    r = B._do_pick(db_session, "m1", "order_table", "card9")
    assert r["ok"] and r["kind"] == "order_table"
    assert db_session.query(Order).filter_by(order_no="W1").count() == 1
    assert patched["mid"] == "card9"                              # 完成后 patch 更新卡片
    assert patched["card"]["header"]["template"] == "green"


def test_do_pick_expired(db_session, monkeypatch):
    monkeypatch.setattr(feishu_client, "patch_card", lambda *a, **k: None)
    assert B._do_pick(db_session, "nope", "order_table", "card9")["error"] == "expired"


def test_on_bitable_change_triggers_sync(monkeypatch):
    """多维表记录变更事件 → 取出 table_id, 后台触发该表同步(长连接接管表格同步, 不再依赖 webhook)。"""
    called = {}
    monkeypatch.setattr(W, "_run_bitable_sync", lambda table_id: called.setdefault("table_id", table_id))

    class _FakeThread:   # 同步替身, 直接跑 target, 不起真线程(避免时序)
        def __init__(self, target=None, args=(), **k):
            self._t, self._a = target, args

        def start(self):
            self._t(*self._a)

    monkeypatch.setattr(W.threading, "Thread", _FakeThread)
    W._on_bitable_change({"event": {"table_id": "tblXYZ", "file_token": "f1"}})
    assert called["table_id"] == "tblXYZ"

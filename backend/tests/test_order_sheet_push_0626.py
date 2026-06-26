# -*- coding: utf-8 -*-
"""下单图推送可靠性修复 (用户 2026-06-26 "三天两头坏"):
   ① 每小时自愈补推 quiet 模式 — 只推单图, 不发 ZIP/无地址提醒(留 18:00 日报)。
   ② 退款作废提醒 — 微信之外也推飞书。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.order import Order
from app.services import order_sheet_archive_service as osa
from app.services import settings_service


@pytest.fixture
def _feishu_stub(monkeypatch):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    monkeypatch.setattr(osa, "render_png", lambda sheet: f"PNG-{sheet.order_no}".encode())
    monkeypatch.setattr("app.services.feishu_client.upload_image", lambda db, png: "img_key")
    monkeypatch.setattr("app.services.feishu_client.send_text", lambda db, cid, text: {"sent": True})
    monkeypatch.setattr("app.services.feishu_client.send_image", lambda db, cid, key: {"sent": True})


def _add_paid_order(db, no: str, day: int = 8):
    db.add(Order(platform="淘宝", order_no=no, qty=1, product_name=f"测试产品{no}",
                 order_date=date(2026, 6, day), status="paid", paid_amount=Decimal("1000")))
    db.flush()


def test_quiet_skips_zip_and_notice(db_session, _feishu_stub, monkeypatch):
    """quiet=True(每小时自愈补推): 单图照推, 但不发 ZIP / 无收货地址提醒。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory")
    zip_calls, notice_calls = [], []
    monkeypatch.setattr(osa, "_send_sheets_zip", lambda db, cid, items: zip_calls.append(len(items)))
    monkeypatch.setattr(osa, "_send_no_addr_notice", lambda db, cid, miss: notice_calls.append(1))

    _add_paid_order(db_session, "Q-1")
    osa.generate_pending(db_session)
    res = osa.push_pending_images(db_session, quiet=True)
    assert res["pushed"] == 1
    assert zip_calls == [] and notice_calls == []   # 静默: 不刷屏

    # 非 quiet 对照(18:00 日报): 仍发 ZIP + 无地址提醒
    _add_paid_order(db_session, "Q-2")
    osa.generate_pending(db_session)
    res2 = osa.push_pending_images(db_session, quiet=False)
    assert res2["pushed"] == 1
    assert zip_calls == [1] and notice_calls == [1]


def test_void_reminder_also_pushes_feishu(db_session, monkeypatch):
    """退款作废提醒: 微信(notify_service)之外, 也推飞书工厂群 (用户 2026-06-26)。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory")
    monkeypatch.setattr(osa, "generate_void_sheets",
                        lambda db, **k: {"voided": 2, "order_nos": ["VO-1", "VO-2"]})
    monkeypatch.setattr("app.services.notify_service.notify", lambda db, text, **k: (True, None))
    sent = []
    monkeypatch.setattr("app.services.feishu_client.send_text",
                        lambda db, cid, text: sent.append((cid, text)))

    res = osa.push_void_daily(db_session)
    assert res["voided"] == 2
    assert res.get("feishu_pushed") is True
    assert len(sent) == 1
    cid, text = sent[0]
    assert cid == "oc_factory"
    assert "作废" in text and "VO-1" in text     # 飞书内容 = 微信同款作废提醒


def test_void_no_feishu_when_nothing_voided(db_session, monkeypatch):
    """无新作废单 → 不打扰(不发飞书)。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory")
    monkeypatch.setattr(osa, "generate_void_sheets", lambda db, **k: {"voided": 0, "order_nos": []})
    sent = []
    monkeypatch.setattr("app.services.feishu_client.send_text",
                        lambda db, cid, text: sent.append(text))
    res = osa.push_void_daily(db_session)
    assert res["pushed"] is False
    assert sent == []

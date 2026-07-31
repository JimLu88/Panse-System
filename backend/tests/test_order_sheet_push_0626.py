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


def _add_paid_order(db, no: str, day: int = 20):
    # 默认 6/20 ≥ _AUTO_NUMBER_SINCE(6/19): 新单会被自动顺排编号, 正常自动推送。
    db.add(Order(platform="淘宝", order_no=no, qty=1, product_name=f"测试产品{no}", sku="标准款",
                 order_date=date(2026, 6, day), status="paid", paid_amount=Decimal("1000"),
                 customer_address="浙江省杭州市西湖区文一路1号"))
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


def test_auto_push_skips_unnumbered_old_order(db_session, _feishu_stub):
    """自动推送(catchup/18:00, include_baseline=False)跳过【无工厂编号】的老单(<6/19),
    不再往工厂群刷"未能匹配工厂订单号"; 手动按钮也必须在补号后才能推。

    根因(用户 2026-06-26 "飞书不停跳"): 每小时 catchup 把从未推过、也没编号的 6/06-6/18 积压老单
    20 张/小时往工厂群灌, 全是红字"未能匹配工厂订单号"。
    """
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory")
    _add_paid_order(db_session, "OLD-1", day=10)   # 6/10 < 6/19 → 不会被自动编号 → factory_no 仍为空
    osa.generate_pending(db_session)

    # 自动路径: 无编号老单被跳过, 一张不推 (remaining 仍计入, 但不再刷屏)
    res = osa.push_pending_images(db_session, include_baseline=False, quiet=True)
    assert res["pushed"] == 0

    # 手动按钮也不能发送无编号图片。
    res2 = osa.push_pending_images(db_session, include_baseline=True)
    assert res2["pushed"] == 0

    # 补齐编号后才允许发送，图片标题、表格第一列和工厂群标题保持一致。
    order = db_session.query(Order).filter_by(order_no="OLD-1").one()
    order.factory_no = 500
    db_session.commit()
    res3 = osa.push_pending_images(db_session, include_baseline=True)
    assert res3["pushed"] == 1


def test_auto_push_sends_numbered_new_order(db_session, _feishu_stub):
    """对照: 新单(≥6/19)自动顺排到工厂编号 → 自动推送照常发出 (不被上面的守卫误伤)。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory")
    _add_paid_order(db_session, "NEW-1", day=20)   # 6/20 ≥ 6/19 → push 内自动顺排编号
    osa.generate_pending(db_session)
    res = osa.push_pending_images(db_session, include_baseline=False, quiet=True)
    assert res["pushed"] == 1
    o = db_session.query(Order).filter_by(order_no="NEW-1").one()
    assert o.factory_no is not None   # 已自动编号


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

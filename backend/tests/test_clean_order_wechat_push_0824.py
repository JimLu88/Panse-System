# -*- coding: utf-8 -*-
"""飞书订单群治噪与微信日报精简（2026-08-24）。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.services import (
    automation_pipeline_service as pipeline,
    notify_service,
    order_sheet_archive_service as sheets,
    sales_analytics,
    scheduler,
    settings_service,
)


def test_critical_pipeline_text_routes_to_wechat_only(db_session, monkeypatch):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    calls = []
    monkeypatch.setattr(
        notify_service,
        "notify",
        lambda db, text, **kwargs: calls.append((text, kwargs)) or (True, "wechat-sent"),
    )
    monkeypatch.setattr(
        "app.services.feishu_client.send_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不得写入飞书订单群")),
    )

    assert pipeline._send_feishu(db_session, "账户余额自动拉取失败") == (True, "wechat-sent")
    assert calls[0][1]["wechat_allowed"] is True
    assert calls[0][1]["title"] == "畔色 ERP | 自动化状态"
    assert calls[0][1]["enqueue_on_failure"] is False


def test_broadcast_text_ignores_legacy_feishu_channel_in_images_only_mode(
    db_session, monkeypatch,
):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    settings_service.set_value(db_session, "notify_text_channels", "feishu,webhook")
    settings_service.set_value(db_session, "notify_provider", "wechat_work")
    settings_service.set_value(db_session, "notify_webhook", "https://qyapi.example/push")
    settings_service.set_value(db_session, "wechat_push_scope", "briefing_only")
    feishu = []
    webhook = []
    monkeypatch.setattr(
        "app.services.feishu_client.send_text",
        lambda *args, **kwargs: feishu.append(args) or {"message_id": "wrong"},
    )
    monkeypatch.setattr(
        notify_service,
        "_post_json",
        lambda url, body: webhook.append((url, body)) or (True, "sent"),
    )

    result = notify_service.broadcast_text(db_session, "运行异常", title="Web-Agent")

    assert result == {"webhook": True}
    assert feishu == []
    assert webhook[0][1]["text"]["content"] == "ℹ️ Web-Agent\n运行异常"


def test_broadcast_text_feishu_split_uses_alert_group_not_order_group(
    db_session, monkeypatch,
):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    settings_service.set_value(db_session, "notify_route_mode", "feishu_split")
    settings_service.set_value(db_session, "feishu_push_chat_id", "factory-chat")
    settings_service.set_value(db_session, "feishu_alert_chat_id", "alert-chat")
    sent = []
    monkeypatch.setattr(
        "app.services.feishu_client.send_text",
        lambda db, chat, text: sent.append((chat, text)) or {"message_id": "alert"},
    )

    result = notify_service.broadcast_text(db_session, "运行异常", title="Web-Agent")

    assert result == {"feishu_alert": True}
    assert sent == [("alert-chat", "ℹ️ Web-Agent\n运行异常")]


def test_update_complete_notice_is_exact_and_once_per_day(db_session, monkeypatch):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    settings_service.set_value(db_session, "feishu_push_chat_id", "factory-chat")
    sent = []
    monkeypatch.setattr(
        "app.services.feishu_client.send_text",
        lambda db, chat, text: sent.append((chat, text)) or {"message_id": "m1"},
    )

    first = sheets.send_order_update_complete_notice(db_session, on_date=date(2026, 8, 24))
    second = sheets.send_order_update_complete_notice(db_session, on_date=date(2026, 8, 24))

    assert first["sent"] is True
    assert second["already_sent"] is True
    assert sent == [("factory-chat", "2026年8月24日订单已完成更新，暂无新增需推送下单图")]


def test_regular_order_delivery_sends_image_without_caption(db_session, monkeypatch):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    settings_service.set_value(db_session, "feishu_push_chat_id", "factory-chat")
    db_session.add(Order(
        platform="淘宝",
        order_no="CLEAN-ORDER-1",
        qty=1,
        product_name="樱桃木床",
        sku="1.8米",
        order_date=date(2026, 8, 24),
        status="paid",
        paid_amount=Decimal("5000"),
        customer_address="浙江省杭州市西湖区测试路1号",
    ))
    db_session.flush()
    monkeypatch.setattr(sheets, "render_png", lambda sheet: b"ORDER-IMAGE")
    sheets.generate_pending(db_session)
    monkeypatch.setattr("app.services.feishu_client.upload_image", lambda db, body: "img-key")
    texts, images = [], []
    monkeypatch.setattr(
        "app.services.feishu_client.send_text",
        lambda db, chat, text: texts.append(text) or {"message_id": "text"},
    )
    monkeypatch.setattr(
        "app.services.feishu_client.send_image",
        lambda db, chat, key: images.append(key) or {"message_id": "image"},
    )

    result = sheets.push_pending_images(db_session, quiet=True)

    assert result["pushed"] == 1
    assert texts == []
    assert images == ["img-key"]


def test_wechat_daily_report_contains_only_requested_sections(db_session, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        sales_analytics,
        "date_summary",
        lambda db, **kwargs: {"date": "2026-08-23", "revenue": 1234.0, "order_count": 2, "top": []},
    )
    monkeypatch.setattr(
        sales_analytics,
        "window_summary",
        lambda db, days, top_n=3: (
            {"days": 7, "revenue": 7000.0, "order_count": 7, "top": []}
            if days == 7 else
            {"days": 30, "revenue": 30000.0, "order_count": 30,
             "top": [{"name": "樱桃木床", "revenue": 12000.0},
                     {"name": "岩板餐桌", "revenue": 8000.0}]}
        ),
    )

    def _notify(db, text, **kwargs):
        captured["text"] = text
        captured["kwargs"] = kwargs
        return True, "sent"

    monkeypatch.setattr(notify_service, "notify", _notify)

    result = scheduler._job_daily_10_comprehensive_report(db_session)
    text = captured["text"]

    assert result["pushed"] is True
    assert "今天日期：" in text
    assert "昨日销售额：¥1,234" in text
    assert "近7天销售额：¥7,000" in text
    assert "近30天销售额：¥30,000" in text
    assert "销售 TOP 榜（近30天）" in text
    assert "1. 樱桃木床 ¥12,000" in text
    for forbidden in ("对账", "异常", "订单数", " 单", "详情登录", "库存", "AI"):
        assert forbidden not in text
    assert captured["kwargs"] == {
        "level": "plain", "title": None, "wechat_allowed": True,
    }

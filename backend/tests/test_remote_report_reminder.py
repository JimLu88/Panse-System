from datetime import date, datetime, timedelta, timezone

from app.models.order import Order
from app.services import (
    feishu_bot_service,
    feishu_client,
    order_flags,
    remote_report_service,
    settings_service,
    taobao_order_import,
)


def _order(**kwargs) -> Order:
    values = {
        "platform": "淘宝",
        "order_no": "REMOTE-REPORT-1",
        "order_date": date(2026, 8, 9),
        "status": "paid",
        "product_name": "榉木餐桌",
        "customer_name": "张先生",
        "is_refill": False,
    }
    values.update(kwargs)
    return Order(**values)


def test_keyword_transition_sends_once_today_and_repeats_next_day(db_session, monkeypatch):
    order = _order(seller_memo="客户要求等通知再发货")
    assert remote_report_service.capture_transition(order, was_remote=False) is True
    db_session.add(order)
    settings_service.set_value(db_session, "feishu_push_chat_id", "chat-1")
    settings_service.set_value(db_session, "feishu_alert_chat_id", "alert-chat")
    settings_service.set_value(db_session, "notify_route_mode", "feishu_split")
    db_session.commit()

    cards = []

    def _send(_db, receive_id, card, **_kwargs):
        cards.append((receive_id, card))
        return {"message_id": f"card-{len(cards)}"}

    monkeypatch.setattr(feishu_client, "send_card", _send)
    first = datetime(2026, 8, 9, 18, 30, tzinfo=timezone(timedelta(hours=8)))
    result = remote_report_service.send_pending_reminders(db_session, now=first)
    assert result == {"ok": True, "due": 1, "sent": 1, "failed": [], "closed": 0}
    assert len(cards) == 1
    assert cards[0][0] == "chat-1"
    assert "等通知" in str(cards[0][1])
    assert order.taobao_remote_report_required is True
    assert order.taobao_remote_report_card_message_id == "card-1"

    same_day = remote_report_service.send_pending_reminders(
        db_session, now=first + timedelta(hours=4)
    )
    assert same_day["due"] == 0
    assert len(cards) == 1

    # PostgreSQL 会把 timestamptz 归一到 UTC；上海凌晨提醒不能因 UTC 仍是前一天而重复。
    order.taobao_remote_report_last_prompt_at = datetime(
        2026, 8, 8, 16, 30, tzinfo=timezone.utc
    )
    utc_same_business_day = remote_report_service.send_pending_reminders(
        db_session,
        now=datetime(2026, 8, 9, 1, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    assert utc_same_business_day["due"] == 0
    assert len(cards) == 1

    next_day = remote_report_service.send_pending_reminders(
        db_session, now=first + timedelta(days=1)
    )
    assert next_day["sent"] == 1
    assert len(cards) == 2
    assert cards[1][0] == "chat-1"
    assert "此前未确认" in str(cards[1][1])


def test_missing_factory_chat_does_not_fallback_to_alert_or_consume_day(db_session, monkeypatch):
    order = _order(seller_memo="等通知")
    remote_report_service.capture_transition(order, was_remote=False)
    db_session.add(order)
    settings_service.set_value(db_session, "feishu_alert_chat_id", "alert-only")
    db_session.commit()
    sent = []
    monkeypatch.setattr(feishu_client, "send_card", lambda *args, **kwargs: sent.append(args))
    result = remote_report_service.send_pending_reminders(db_session)
    assert result["ok"] is False
    assert result["sent"] == 0
    assert result["failed"][0]["reason"] == "未配置飞书工厂下单群"
    assert sent == []
    assert order.taobao_remote_report_last_prompt_at is None
    assert order.taobao_remote_report_required is True


def test_manual_remote_or_existing_remote_does_not_create_report_task(db_session):
    manual = _order(order_no="MANUAL", is_remote_ship=True)
    assert remote_report_service.capture_transition(manual, was_remote=False) is False
    assert manual.taobao_remote_report_required is not True

    existing = _order(order_no="EXISTING", seller_memo="等通知")
    assert order_flags.is_remote(existing) is True
    assert remote_report_service.capture_transition(existing, was_remote=True) is False
    assert existing.taobao_remote_report_required is not True


def test_activation_closes_pending_report_and_new_remote_episode_reopens(db_session):
    order = _order(seller_memo="等通知")
    remote_report_service.capture_transition(order, was_remote=False)
    assert order.taobao_remote_report_required is True

    was_remote = order_flags.is_remote(order)
    order.production_note = "客户已经开始制作"
    remote_report_service.capture_transition(order, was_remote=was_remote)
    assert order.taobao_remote_report_required is False

    was_remote = order_flags.is_remote(order)
    order.production_note = ""
    order.seller_memo = "再次等通知"
    assert remote_report_service.capture_transition(order, was_remote=was_remote) is True
    assert order.taobao_remote_report_required is True
    assert order.taobao_remote_report_confirmed_at is None


def test_card_confirmation_is_idempotent_and_stops_reminders(db_session, monkeypatch):
    order = _order(seller_memo="延迟发货，等通知")
    remote_report_service.capture_transition(order, was_remote=False)
    db_session.add(order)
    db_session.commit()

    patched = []
    monkeypatch.setattr(
        feishu_client,
        "patch_card",
        lambda _db, message_id, card: patched.append((message_id, card)) or {},
    )
    event = {
        "action": {
            "value": {
                "op": "confirm_remote_report",
                "order_no": order.order_no,
            }
        },
        "context": {"open_message_id": "card-message-1"},
    }
    result = feishu_bot_service.on_card_action(db_session, event)
    assert result["ok"] is True
    assert order.taobao_remote_report_required is False
    assert order.taobao_remote_report_confirmed_at is not None
    assert patched and patched[0][0] == "card-message-1"
    assert "已确认" in str(patched[0][1])

    again = remote_report_service.confirm(db_session, order.order_no)
    assert again["ok"] is True
    assert again["card"]["header"]["template"] == "green"


def test_send_failure_does_not_consume_daily_reminder(db_session, monkeypatch):
    order = _order(seller_memo="晚点发货")
    remote_report_service.capture_transition(order, was_remote=False)
    db_session.add(order)
    settings_service.set_value(db_session, "feishu_push_chat_id", "chat-1")
    db_session.commit()

    monkeypatch.setattr(
        feishu_client,
        "send_card",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    result = remote_report_service.send_pending_reminders(db_session)
    db_session.refresh(order)
    assert result["ok"] is False
    assert result["failed"][0]["order_no"] == order.order_no
    assert order.taobao_remote_report_last_prompt_at is None
    assert order.taobao_remote_report_required is True


def test_taobao_reimport_creates_task_only_when_remark_changes_to_remote(db_session):
    order = _order(order_no="IMPORT-TRANSITION", seller_memo="普通订单")
    db_session.add(order)
    db_session.commit()

    row = taobao_order_import._OrderRow(
        order_no=order.order_no,
        order_date="2026-08-09",
        status_text="买家已付款，等待卖家发货",
        paid_real="100",
        seller_memo="客户说延迟发货，等通知",
    )
    report = taobao_order_import.TaobaoImportReport(detected_format="order_master")
    taobao_order_import._commit_orders(
        db_session, {order.order_no: row}, "淘宝", report
    )
    db_session.refresh(order)
    assert report.updated == 1
    assert order.taobao_remote_report_required is True
    assert order.taobao_remote_report_keyword in {"等通知", "延迟发", "延迟发货"}

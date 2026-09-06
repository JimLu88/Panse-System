"""淘宝远期单后台报备提醒。

只有订单从非远期状态因备注关键词变成远期挂起时才创建待办。提醒使用订单表上的
持久化字段去重：同一天最多一张卡；没有确认则下一次自然日订单拉取完成后再提醒。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.services import feishu_client, order_flags, settings_service


_log = logging.getLogger("panse.remote_report")
_CLOSED_STATUSES = {"cancelled", "shipped", "signed"}


def matched_keyword(order: Order) -> Optional[str]:
    """返回命中的首个远期关键词，供卡片解释触发原因。"""
    text = order_flags.order_text(order)
    return next((keyword for keyword in order_flags.REMOTE_KW if keyword in text), None)


def is_keyword_remote(order: Order) -> bool:
    """当前确为“关键词导致的远期挂起”，激活词优先解除。"""
    return bool(matched_keyword(order)) and order_flags.is_remote(order)


def capture_transition(order: Order, *, was_remote: bool) -> bool:
    """把一次非远期→关键词远期转换落成待报备；返回本次是否新建待办。

    人工勾选远期、客户延期字段、日期式远期不会单独触发。远期被激活或备注不再命中
    时结束当前待办；以后再次从普通状态转成关键词远期，会开启新的确认周期。
    """
    keyword = matched_keyword(order)
    now_keyword_remote = bool(keyword) and order_flags.is_remote(order)
    platform = str(getattr(order, "platform", "") or "")
    is_taobao = "淘宝" in platform or "天猫" in platform

    if is_taobao and not was_remote and now_keyword_remote:
        order.taobao_remote_report_required = True
        order.taobao_remote_report_confirmed_at = None
        order.taobao_remote_report_last_prompt_at = None
        order.taobao_remote_report_card_message_id = None
        order.taobao_remote_report_keyword = keyword
        return True

    if not now_keyword_remote and bool(order.taobao_remote_report_required):
        order.taobao_remote_report_required = False
        order.taobao_remote_report_last_prompt_at = None
        order.taobao_remote_report_card_message_id = None
    return False


def _order_label(order: Order) -> str:
    return order_flags.factory_label(order) or f"订单 {order.order_no}"


def reminder_card(order: Order, *, reminder: bool = False) -> dict:
    keyword = order.taobao_remote_report_keyword or matched_keyword(order) or "远期备注"
    product = (order.product_name or order.sku or "未填写产品")[:80]
    customer = (order.customer_name or "未填写客户")[:40]
    lead = "此前未确认，继续提醒。" if reminder else "订单刚因备注关键词转为远期单。"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "⏳ 远期单淘宝报备待确认"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"{lead}\n"
                        f"**{_order_label(order)}** · 淘宝订单 `{order.order_no}`\n"
                        f"客户：{customer}\n产品：{product}\n"
                        f"命中备注关键词：**{keyword}**\n\n"
                        "请先在淘宝后台完成延迟发货/远期订单报备，再点击下面按钮。"
                        "如果今天还没完成，不用点击；下次自然日订单拉取后会继续提醒。"
                    ),
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "☑ 已完成淘宝后台报备"},
                        "type": "primary",
                        "value": {
                            "op": "confirm_remote_report",
                            "order_no": order.order_no,
                        },
                    }
                ],
            },
        ],
    }


def confirmed_card(order: Order, confirmed_at: datetime) -> dict:
    timestamp = confirmed_at.astimezone().strftime("%Y-%m-%d %H:%M")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "✅ 淘宝后台报备已确认"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**{_order_label(order)}** · 淘宝订单 `{order.order_no}`\n"
                        f"确认时间：{timestamp}\n后续不再发送此轮远期报备提醒。"
                    ),
                },
            }
        ],
    }


def _eligible(order: Order) -> bool:
    if not order.taobao_remote_report_required or order.taobao_remote_report_confirmed_at:
        return False
    if order.status in _CLOSED_STATUSES:
        return False
    return is_keyword_remote(order)


def send_pending_reminders(db: Session, *, now: Optional[datetime] = None) -> dict:
    """发送今天尚未提醒的待报备卡片；发送成功后才落去重时间。"""
    current = (now or datetime.now().astimezone()).astimezone()
    # 用户指定此类报备卡片随工厂下单走订单群，不改变其他告警的分流。
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False) or ""
    orders = db.execute(
        select(Order)
        .where(Order.taobao_remote_report_required.is_(True))
        .order_by(Order.order_date.asc(), Order.id.asc())
        .with_for_update(skip_locked=True)
    ).scalars().all()

    due: list[Order] = []
    closed = 0
    for order in orders:
        if not _eligible(order):
            order.taobao_remote_report_required = False
            order.taobao_remote_report_last_prompt_at = None
            order.taobao_remote_report_card_message_id = None
            closed += 1
            continue
        last = order.taobao_remote_report_last_prompt_at
        if last is not None:
            last_local = (
                last.astimezone(current.tzinfo)
                if last.tzinfo is not None
                else last.replace(tzinfo=current.tzinfo)
            )
        else:
            last_local = None
        if last_local is not None and last_local.date() >= current.date():
            continue
        due.append(order)

    if not due:
        if closed:
            db.commit()
        return {"ok": True, "due": 0, "sent": 0, "failed": [], "closed": closed}
    if not chat_id:
        if closed:
            db.commit()
        return {
            "ok": False,
            "due": len(due),
            "sent": 0,
            "failed": [{"order_no": order.order_no, "reason": "未配置飞书工厂下单群"} for order in due],
            "closed": closed,
        }

    sent = 0
    failed: list[dict] = []
    for order in due:
        try:
            reminder = order.taobao_remote_report_last_prompt_at is not None
            data = feishu_client.send_card(
                db, chat_id, reminder_card(order, reminder=reminder)
            ) or {}
            order.taobao_remote_report_last_prompt_at = current
            order.taobao_remote_report_card_message_id = data.get("message_id")
            sent += 1
            db.commit()
        except Exception as exc:  # noqa: BLE001 - keep remaining orders retryable
            db.rollback()
            failed.append({"order_no": order.order_no, "reason": f"{type(exc).__name__}: {exc}"})
            _log.warning("远期单报备卡片发送失败 order=%s: %s", order.order_no, exc)
    return {
        "ok": not failed,
        "due": len(due),
        "sent": sent,
        "failed": failed,
        "closed": closed,
    }


def confirm(db: Session, order_no: str, *, now: Optional[datetime] = None) -> dict:
    """确认淘宝后台报备，幂等销账并返回用于 patch 的结果卡片。"""
    order = db.execute(select(Order).where(Order.order_no == str(order_no))).scalar_one_or_none()
    if order is None:
        return {"ok": False, "error": "order_not_found", "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": "red", "title": {"tag": "plain_text", "content": "订单不存在"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "没有找到对应订单，未完成报备确认。"}}],
        }}
    confirmed_at = order.taobao_remote_report_confirmed_at or (
        now or datetime.now().astimezone()
    ).astimezone()
    order.taobao_remote_report_required = False
    order.taobao_remote_report_confirmed_at = confirmed_at
    db.commit()
    return {"ok": True, "order_no": order.order_no, "card": confirmed_card(order, confirmed_at)}

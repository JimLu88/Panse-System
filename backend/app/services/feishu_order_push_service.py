"""Trusted Feishu commands for the existing order collection/delivery workflow."""
import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone

from sqlalchemy import text as sql_text
from app.services import settings_service, feishu_client

log = logging.getLogger(__name__)
WORDS = {"推送", "推单", "推送订单", "补推订单"}
ACTIVE_KEY = "feishu_order_push_active_v1"
_LOCK = threading.Lock()


def _load(db, key):
    try:
        return json.loads(settings_service.get(db, key, env_fallback=False) or "{}")
    except (TypeError, ValueError):
        return {"state": "unknown"}


def _save(db, key, value):
    settings_service.set_value(db, key, json.dumps(value, ensure_ascii=False))
    if _load(db, ACTIVE_KEY).get("key") == key:
        settings_service.set_value(db, ACTIVE_KEY, json.dumps(value, ensure_ascii=False))
    db.commit()


def _card(title, message, color="blue"):
    return {"header": {"template": color, "title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": message}}]}


def _reply(db, message_id, title, message, color="blue"):
    return feishu_client.reply_card(db, message_id, _card(title, message, color))


def status_text(value):
    state = value.get("state")
    if state in {"claimed", "running"}:
        return "推单任务已受理，尚未收到结束结果。已发送的下单图不会重复发送。如长时间未结束，请联系程序维护核对原任务。"
    if state == "unknown":
        return "上次执行结果待核对，已保留原记录，未重复启动。请联系程序维护核对。"
    return value.get("message") or "当前没有从飞书发起的推单任务。登录后发送「推送」即可启动。"


def handle_message(db, event):
    msg = event.get("message") or {}
    kind = msg.get("message_type")
    if kind not in {"text", "post"}:
        return None
    try:
        content = json.loads(msg.get("content") or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(content, dict):
        return None
    if kind == "post":
        content = content.get("zh_cn", content)
        if not isinstance(content, dict):
            return None
        from app.services.feishu_bot_service import _post_text, _post_image_keys
        if _post_image_keys(content):
            return None
        value = _post_text(content)
    else:
        value = content.get("text", "")
    value = re.sub(r"@_user_\d+|@_all|@\S+", "", value or "").strip()
    if value not in WORDS | {"推送状态"}:
        return None
    mid, chat = msg.get("message_id"), msg.get("chat_id")
    sender = event.get("sender") or {}
    actor = (sender.get("sender_id") or {}).get("open_id")
    if not event.get("_factory_forward_trusted") or not mid or not actor or sender.get("sender_type") != "user":
        return {"kind": "order_push", "started": False, "reason": "untrusted_sender"}
    allowed = {settings_service.get(db, k, env_fallback=False) for k in ("feishu_push_chat_id", "feishu_alert_chat_id")}
    if not chat or chat not in allowed:
        _reply(db, mid, "推单未启动", "请在已配置的订单群或提醒群中 @机器人发送「推送」。")
        return {"kind": "order_push", "started": False, "reason": "wrong_chat"}
    if value == "推送状态":
        _reply(db, mid, "推送状态", status_text(_load(db, ACTIVE_KEY)))
        return {"kind": "order_push_status", "started": False}
    from app.services import agent_ingest_service as ingest, platform_scan_service
    key = "feishu_order_push_" + hashlib.sha256(mid.encode()).hexdigest()
    with _LOCK:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(sql_text("SELECT pg_advisory_xact_lock(20261124, 1)"))
        if _load(db, key):
            db.rollback()
            return {"kind": "order_push", "started": False, "reason": "duplicate_message"}
        active = _load(db, ACTIVE_KEY)
        login = _load(db, platform_scan_service.ACTIVE_KEY)
        if active.get("state") in {"claimed", "running", "unknown"}:
            reason = status_text(active)
        elif ingest.is_running():
            reason = "已有取数任务进行中，未重复启动。请稍后发送「推送」。"
        elif login.get("state") in {"claimed", "running", "unknown"}:
            reason = "登录流程尚未完成，请先完成登录；回复「扫码状态」可查看进度。"
        else:
            reason = None
        row = {"key": key, "message_id": mid, "chat_id": chat, "actor": actor,
               "state": "blocked" if reason else "claimed", "message": reason,
               "requested_at": datetime.now(timezone.utc).isoformat()}
        settings_service.set_value(db, key, json.dumps(row, ensure_ascii=False))
        if not reason:
            settings_service.set_value(db, ACTIVE_KEY, json.dumps(row, ensure_ascii=False))
        db.commit()
    if reason:
        _reply(db, mid, "推单未重复启动", reason)
        return {"kind": "order_push", "started": False, "reason": reason}
    # A failed acknowledgement does not silently drop an accepted durable request.
    try:
        _reply(db, mid, "已收到推送指令", "开始核对最新订单并补推未发送的下单图，完成后在这里回复结果。若需重新登录或补充数量，会明确提示。")
    except Exception:
        log.warning("Order push acknowledgement unavailable", exc_info=True)
    try:
        _launch(key)
    except Exception:
        row.update(state="unknown", message="任务启动结果待核对，未自动重试。")
        _save(db, key, row)
        raise
    return {"kind": "order_push", "started": True, "request_key": key}


def _launch(key):
    threading.Thread(target=run_request, args=(key,), daemon=True, name="feishu-order-push").start()


def run_request(key):
    from app.database import SessionLocal
    from app.services import scheduler
    with SessionLocal() as db:
        with _LOCK:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(sql_text("SELECT pg_advisory_xact_lock(20261124, 1)"))
            row = _load(db, key)
            if row.get("state") != "claimed":
                db.rollback()
                return
            row.update(state="running", started_at=datetime.now(timezone.utc).isoformat())
            _save(db, key, row)
        try:
            result = scheduler._job_web_agent_daily(db)
            if not isinstance(result, dict) or (not result.get("delivery")
                    and result.get("_run_status") not in {"fail", "skipped"}
                    and not result.get("skipped")):
                raise RuntimeError("missing_delivery_result")
            delivery = result.get("delivery") or {}
            pushed = int(delivery.get("images_pushed") or 0)
            failed = result.get("_run_status") == "fail"
            skipped = bool(result.get("skipped")) or result.get("_run_status") == "skipped"
            if failed or skipped:
                reason = str(result.get("_error") or result.get("skipped") or "本轮未完成")[:700]
                message = f"本轮已推送 {pushed} 张下单图，其余尚未完成。原因：{reason}"
                if "登录" in reason or "扫码" in reason:
                    message += "\n请发送「扫码」选择店铺后台，登录完成后再发送「推送」。"
                state = "blocked"
            else:
                message = f"本轮已完成订单更新，新增推送 {pushed} 张下单图。已成功发送的图不会重复推送。"
                held = delivery.get("held_no_address") or []
                if held:
                    message += f"\n另有 {len(held)} 单因地址不完整暂缓，补齐后再推。"
                state = "completed"
            row.update(state=state, message=message, images_pushed=pushed,
                       finished_at=datetime.now(timezone.utc).isoformat())
            _save(db, key, row)
        except Exception as exc:
            db.rollback()
            row.update(state="unknown", message="本轮执行中断，结果待核对。已保留原记录，请勿重复启动。",
                       error_type=type(exc).__name__)
            _save(db, key, row)
            log.exception("Feishu order push interrupted")
        row["result_notice"] = "sending"
        _save(db, key, row)
        try:
            receipt = _reply(db, row["message_id"], "订单推送结果", row["message"],
                             "green" if row["state"] == "completed" else "orange")
            if not receipt.get("message_id"):
                raise RuntimeError("missing_message_receipt")
            row.update(result_notice="sent", result_message_id=receipt["message_id"])
        except Exception:
            row["result_notice"] = "unknown"  # Status query remains available; never blindly resend.
        _save(db, key, row)

"""飞书机器人长连接(WebSocket)接入 — 自建应用免公网地址 / 免验签。

用官方 lark-oapi WS 客户端收两类事件, 复用 feishu_bot_service 的识别/入库逻辑:
  - im.message.receive_v1   收到图片 → 下载 → 分类 → 回卡片(确认/选类型)
  - card.action.trigger     卡片按钮 → 3秒内 ack 一个 toast, 入库放后台线程 + patch 更新卡片

仅当配了 app_id/secret 且环境变量 ENABLE_FEISHU_BOT=1 时启动(由 main.py lifespan 调 start())。
启动失败/凭证缺失只记日志, 不影响主服务。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

_log = logging.getLogger("panse.feishu_ws")

_thread: Optional[threading.Thread] = None
_started = False


def is_running() -> bool:
    return _started


def _new_session():
    from app.database import SessionLocal
    return SessionLocal()


# ── 事件解析 (纯函数, 便于测试) ────────────────────────────────
def _to_dict(data: Any) -> dict:
    """lark 事件对象 → dict (marshal); 已是 dict 则透传。取其中 event 子树。"""
    if isinstance(data, dict):
        raw = data
    else:
        import lark_oapi as lark
        raw = json.loads(lark.JSON.marshal(data))
    return raw.get("event") or raw


def _parse_card_event(event: dict) -> tuple[Optional[str], dict, Optional[str]]:
    """卡片回调 → (op, value, card_message_id)。"""
    action = event.get("action") or {}
    value = action.get("value") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    op = value.get("op")
    card_msg_id = (event.get("context") or {}).get("open_message_id")
    return op, value, card_msg_id


# ── 事件 handler ──────────────────────────────────────────────
def _on_message(data: Any) -> None:
    """im.message.receive_v1: 收图 → 识别 → 回卡片 (事件类无需返回)。"""
    from app.services import feishu_bot_service
    event = _to_dict(data)
    db = _new_session()
    try:
        feishu_bot_service.on_message_event(db, event)
        db.commit()
    except Exception as e:  # pragma: no cover
        db.rollback()
        _log.error("WS 收图处理失败: %s", e)
    finally:
        db.close()


def _on_card(data: Any):
    """card.action.trigger: 3秒内回 toast; "确认入库"放后台线程, 完成后 patch 卡片。"""
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    from app.services import feishu_bot_service as B

    op, value, card_msg_id = _parse_card_event(_to_dict(data))
    orig_id = value.get("message_id")

    if op == "pick" and orig_id:
        kind = value.get("kind")
        # 送货单需先追问"哪家供应商"才能正确归属, 不直接入库
        if kind == "supplier_note":
            db = _new_session()
            try:
                _patch(card_msg_id, B._supplier_picker_card(orig_id, B._recent_suppliers(db)))
            finally:
                db.close()
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请选择归属供应商"}})
        threading.Thread(
            target=B.process_pick, args=(orig_id, kind, card_msg_id), daemon=True,
        ).start()
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已收到，正在识别入库…"}})
    if op == "pick_supplier" and orig_id:
        supplier_id = value.get("supplier_id")
        threading.Thread(
            target=B.process_pick_supplier, args=(orig_id, supplier_id, card_msg_id), daemon=True,
        ).start()
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已收到，正在入库…"}})
    if op == "repick" and orig_id:
        _patch(card_msg_id, B._picker_card(orig_id))
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请重新选择类型"}})
    if op == "repick_file" and orig_id:
        db = _new_session()
        try:
            pending = B._load_pending(db).get(orig_id) or {}
        finally:
            db.close()
        _patch(card_msg_id, B._file_picker_card(orig_id, pending.get("file_name", "")))
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请重新选择类型"}})
    if op == "cancel":
        if card_msg_id:
            _patch(card_msg_id, B._result_card("已取消", "好的，这张图不入库。", "grey"))
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已取消"}})
    return P2CardActionTriggerResponse({"toast": {"type": "warning", "content": "无效操作"}})


def _patch(card_msg_id: Optional[str], card: dict) -> None:
    if not card_msg_id:
        return
    from app.services import feishu_client
    db = _new_session()
    try:
        feishu_client.patch_card(db, card_msg_id, card)
    except Exception as e:  # pragma: no cover
        _log.warning("WS 更新卡片失败: %s", e)
    finally:
        db.close()


# ── 启停 ──────────────────────────────────────────────────────
def start() -> bool:
    """启动长连接客户端(后台 daemon 线程)。仅当配了凭证才起; 返回是否启动。"""
    global _thread, _started
    if _started:
        return True
    from app.services import feishu_client
    db = _new_session()
    try:
        app_id, app_secret = feishu_client.get_credentials(db)
    except Exception:
        _log.info("飞书机器人长连接未启动: 未配置 app_id/secret")
        return False
    finally:
        db.close()
    if not (app_id and app_secret):
        return False

    import lark_oapi as lark
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .register_p2_card_action_trigger(_on_card)
        .build()
    )
    client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.WARNING)

    def _run():
        try:
            client.start()  # 阻塞: 维持长连接
        except Exception as e:  # pragma: no cover
            _log.error("飞书机器人长连接退出: %s", e)

    _thread = threading.Thread(target=_run, name="feishu-ws", daemon=True)
    _thread.start()
    _started = True
    _log.info("飞书机器人长连接已启动")
    return True

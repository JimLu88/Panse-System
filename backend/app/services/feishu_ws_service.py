"""飞书机器人长连接(WebSocket)接入 — 自建应用免公网地址 / 免验签。

用官方 lark-oapi WS 客户端收三类事件, 复用既有识别/入库/同步逻辑:
  - im.message.receive_v1              收到图片/文件 → 下载 → 分类 → 回卡片(确认/选类型)
  - card.action.trigger               卡片按钮 → 3秒内 ack 一个 toast, 入库放后台线程 + patch 更新卡片
  - drive.file.bitable_record_changed 多维表记录变更 → 后台触发表格同步(免再依赖 webhook)

仅当配了 app_id/secret 且环境变量 ENABLE_FEISHU_BOT=1 时启动(由 main.py lifespan 调 start())。
启动失败/凭证缺失只记日志, 不影响主服务。
"""
from __future__ import annotations

import collections
import json
import logging
import queue
import threading
import time
from typing import Any, Optional

_log = logging.getLogger("panse.feishu_ws")

_thread: Optional[threading.Thread] = None
_started = False


def is_running() -> bool:
    return _started


def _new_session():
    from app.database import SessionLocal
    return SessionLocal()


# ── 事件去重 + 消息异步处理 ──────────────────────────────────────
# 飞书长连接: handler 若不能秒回, 服务端会判失败并「重复、反复重发」同一事件。
# (1) 按 event_id 去重, 重发的事件直接忽略;
# (2) 收图/收文件的识别+OCR 较慢 → 不在回调里同步做, 丢进单后台 worker 串行处理,
#     回调立刻返回 → 不再触发重发(取消后被重发的选类型卡"弹回"也随之消失)。
_seen_events: "collections.OrderedDict[str, bool]" = collections.OrderedDict()
_seen_lock = threading.Lock()
_SEEN_CAP = 4000

_msg_queue: "queue.Queue[dict]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def _marshal(data: Any) -> dict:
    """lark 事件对象 → 完整 raw dict(含 header/event); 已是 dict 则透传。"""
    if isinstance(data, dict):
        return data
    import lark_oapi as lark
    return json.loads(lark.JSON.marshal(data))


def _evt_id(raw: dict) -> Optional[str]:
    return (raw.get("header") or {}).get("event_id")


def _dedup(event_id: Optional[str]) -> bool:
    """True=重复事件(应跳过)。无 event_id 不去重(如单测直接喂 dict)。"""
    if not event_id:
        return False
    with _seen_lock:
        if event_id in _seen_events:
            return True
        _seen_events[event_id] = True
        while len(_seen_events) > _SEEN_CAP:
            _seen_events.popitem(last=False)
    return False


# 飞书会对"未成功 ack 的旧事件"长时间反复重投(可达数小时)。进程重启会清空内存去重,
# 于是每次重启后这些旧事件的下一次重投又会弹一张卡 —— 表现为"一直默默地发卡片"。
# 用消息创建时间兜底: 超过 15 分钟的消息事件一律视为旧重投, 直接 ack 不再弹卡(与重启无关)。
_STALE_MS = 15 * 60 * 1000


def _is_stale_ms(create_time_ms) -> bool:
    try:
        return (time.time() * 1000 - float(create_time_ms)) > _STALE_MS
    except (TypeError, ValueError):
        return False


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_msg_worker, name="feishu-msg-worker", daemon=True).start()
        _worker_started = True


def _msg_worker() -> None:
    """串行处理收到的消息事件(下载/识别/归档/回卡)。串行 → 3 分钟归批逻辑不并发打架。"""
    from app.services import feishu_bot_service
    while True:
        event = _msg_queue.get()
        try:
            db = _new_session()
            try:
                feishu_bot_service.on_message_event(db, event)
                db.commit()
            except Exception as e:  # pragma: no cover
                db.rollback()
                _log.exception("WS 消息处理失败: %s", e)
                _handle_worker_failure(db, event, e)
            finally:
                db.close()
        except Exception as e:  # pragma: no cover
            _log.error("WS 消息 worker 异常: %s", e)
        finally:
            _msg_queue.task_done()


def _handle_worker_failure(db, event: dict, exc: Exception) -> dict:
    """Persist password-callback failures and always return a safe Feishu receipt.

    Never echo inbound text: it may contain the one-time shipping password.
    """
    from app.services import (
        automation_failure_recorder_service,
        feishu_bot_service,
    )

    msg = event.get("message") or {}
    message_id = str(msg.get("message_id") or "").strip()
    is_password = False
    if msg.get("message_type") == "text":
        try:
            content = json.loads(msg.get("content") or "{}")
            is_password = "密码" in str(content.get("text") or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            is_password = False

    recorded = None
    try:
        if is_password and message_id:
            recorded = automation_failure_recorder_service.record_callback_run(
                db,
                category="order",
                status="fail",
                detail=f"飞书发货密码回调中断: {type(exc).__name__}",
                recovery_key=f"feishu-message:{message_id}",
                result_summary={
                    "source": "shipping_password",
                    "message_id": message_id,
                    "error_type": type(exc).__name__,
                },
            )
        if message_id:
            feishu_bot_service._safe_reply(
                db,
                message_id,
                feishu_bot_service._result_card(
                    "指令处理未完成",
                    f"机器人已收到消息，但后台处理异常（{type(exc).__name__}）。"
                    "本次后续操作未完成，失败原因已记录；无需反复发送同一条消息。",
                    "red",
                ),
            )
        db.commit()
    except Exception:  # pragma: no cover - final safety net must never kill worker
        db.rollback()
        _log.exception("WS 消息失败回执或失败事件记录未完成")
    return {"message_id": message_id or None, "recorded": recorded}


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
    """im.message.receive_v1: 秒回(只入队), 实际识别在后台串行做。多重防重发:
    (1) 按 message_id 去重(同一条消息的重投不再处理); (2) 丢弃飞书对旧事件的长时间重投
    (按 create_time, 超 15 分钟直接 ack 不弹卡) —— 彻底止住"一直默默发卡片"。
    """
    raw = _marshal(data)
    event = raw.get("event") or raw
    msg = event.get("message") or {}
    key = msg.get("message_id") or _evt_id(raw)
    if _dedup(key):
        _log.info("WS 重复消息已忽略(去重 key=%s)", key)
        return
    ct = msg.get("create_time")
    if ct and _is_stale_ms(ct):
        _log.info("WS 丢弃飞书旧事件重投(create_time=%s, 不弹卡, 直接 ack)", ct)
        return
    _ensure_worker()
    _msg_queue.put(event)
    _log.info("WS 收到消息事件入队 message_id=%s", msg.get("message_id"))


def _on_card(data: Any):
    """card.action.trigger: 3秒内回 toast; "确认入库"放后台线程, 完成后 patch 卡片。"""
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    from app.services import feishu_bot_service as B

    raw = _marshal(data)
    if _dedup(_evt_id(raw)):   # 重发的同一次点击 → 不重复处理(避免重复入库/重复弹卡)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "处理中…"}})
    op, value, card_msg_id = _parse_card_event(raw.get("event") or raw)
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


def _on_bitable_change(data: Any) -> None:
    """drive.file.bitable_record_changed_v1: 多维表记录变更 → 后台触发对应绑定同步。

    长连接接管表格同步事件后, 飞书改完不必再依赖 webhook 也能近实时同步(另有 30 分钟定时兜底)。
    同步要打飞书 API + 写库, 可能较慢 → 放后台线程, 不占长连接 3 秒回执窗口。
    """
    event = _to_dict(data)
    table_id = event.get("table_id")
    threading.Thread(target=_run_bitable_sync, args=(table_id,),
                     name="feishu-ws-sync", daemon=True).start()


def _run_bitable_sync(table_id: Optional[str]) -> None:
    """后台: 找到该多维表对应的启用绑定, 逐个同步(尽力而为, 单个失败不连累其余)。"""
    from sqlalchemy import select
    from app.models.feishu_sync import FeishuTableBinding
    from app.services import feishu_sync_service

    db = _new_session()
    try:
        q = select(FeishuTableBinding).where(FeishuTableBinding.enabled.is_(True))
        if table_id:
            q = q.where(FeishuTableBinding.feishu_table_id == table_id)
        for b in db.execute(q).scalars().all():
            try:
                feishu_sync_service.sync_binding(db, b)
                db.commit()
                _log.info("长连接触发同步 %s", getattr(b, "system_table", "?"))
            except Exception as e:  # pragma: no cover
                db.rollback()
                _log.error("长连接同步 %s 失败: %s", getattr(b, "system_table", "?"), e)
    finally:
        db.close()


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

    def _run():
        # 关键: lark ws 客户端要在本线程内构造 + 用本线程自己的事件循环,
        # 否则在主线程(uvicorn 运行中的 loop)里构造会捕获那个 loop, start() 时报
        # "this event loop is already running"。
        import asyncio
        import lark_oapi as lark
        asyncio.set_event_loop(asyncio.new_event_loop())
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_on_message)
            .register_p2_card_action_trigger(_on_card)
            .register_p2_drive_file_bitable_record_changed_v1(_on_bitable_change)
            .build()
        )
        client = lark.ws.Client(
            app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.WARNING)
        try:
            client.start()  # 阻塞: 维持长连接
        except Exception as e:  # pragma: no cover
            _log.error("飞书机器人长连接退出: %s", e)

    _thread = threading.Thread(target=_run, name="feishu-ws", daemon=True)
    _thread.start()
    _started = True
    _log.info("飞书机器人长连接已启动")
    return True

"""关键自动化的逐次失败告警、有限重试状态和飞书必达队列。

只覆盖用户明确指定的三条链路：

* 订单自动推送
* 账户余额自动拉取
* 收支流水自动拉取

每个自然日独立计数。首次失败立即告知下一次重试时间；每次重试失败都再次
告知；当天重试用尽后明确发送“今日失败”。失败后恢复成功只发一条收口通知。
财务类通知不携带余额、收入、支出等金额。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.services import settings_service

SETTING_KEY = "critical_automation_pipeline_state_v1"
_DESCRIPTION = "关键自动化失败/重试/最终失败状态及飞书待发送队列"
_MAX_NOTIFY_ATTEMPTS = 8

PIPELINE_LABELS = {
    "order_delivery": "订单自动推送",
    "balance_pull": "账户余额自动拉取",
    "flow_pull": "收支流水自动拉取",
}


def _now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now().astimezone()
    return current if current.tzinfo is not None else current.astimezone()


def _default() -> dict:
    return {"pipelines": {}, "notification_queue": []}


def _load(db: Session) -> dict:
    raw = settings_service.get(db, SETTING_KEY, env_fallback=False)
    if not raw:
        return _default()
    try:
        state = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _default()
    if not isinstance(state, dict):
        return _default()
    state.setdefault("pipelines", {})
    state.setdefault("notification_queue", [])
    if not isinstance(state["pipelines"], dict):
        state["pipelines"] = {}
    if not isinstance(state["notification_queue"], list):
        state["notification_queue"] = []
    return state


def _save(db: Session, state: dict) -> None:
    # 只保留最近 100 条待发/已耗尽记录，防配置无限膨胀。
    state["notification_queue"] = state.get("notification_queue", [])[-100:]
    settings_service.set_value(
        db,
        SETTING_KEY,
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        description=_DESCRIPTION,
    )


def _safe_error(error: str) -> str:
    """财务告警不外发金额；同时限制异常文本长度。"""
    text = str(error or "未返回明确原因")
    text = re.sub(r"[¥￥]\s*[\d,.]+", "金额已隐藏", text)
    text = re.sub(r"\b\d+(?:\.\d{1,2})?\s*元\b", "金额已隐藏", text)
    return text[:500]


def _send_feishu(db: Session, text: str) -> tuple[bool, str]:
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return False, "disabled"
    try:
        from app.services import feishu_client

        chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
        if not chat_id:
            return False, "未配置飞书接收会话"
        feishu_client.send_text(db, chat_id, text)
        return True, "sent"
    except Exception as exc:  # noqa: BLE001 - 失败进入持久队列
        return False, f"{type(exc).__name__}: {exc}"[:300]


def _deliver_or_queue(
    db: Session,
    state: dict,
    *,
    dedupe_key: str,
    text: str,
    now: datetime,
) -> dict:
    queue = state.setdefault("notification_queue", [])
    queue_prefix = ":".join(dedupe_key.split(":")[:2]) + ":"
    earlier = [
        item for item in queue
        if str(item.get("dedupe_key") or "").startswith(queue_prefix)
        and not item.get("sent_at")
        and not item.get("exhausted")
    ]
    # 同一链路的失败、恢复通知必须按发生顺序抵达，不能先看到“恢复”再补收到旧失败。
    if earlier:
        next_times = [now]
        for item in earlier:
            try:
                next_times.append(_now(datetime.fromisoformat(str(item.get("next_at") or ""))))
            except (TypeError, ValueError):
                pass
        if not any(q.get("dedupe_key") == dedupe_key for q in queue):
            queue.append({
                "id": uuid4().hex,
                "dedupe_key": dedupe_key,
                "text": text,
                "attempts": 0,
                "next_at": max(next_times).isoformat(),
                "created_at": now.isoformat(),
                "last_error": "等待更早通知先发送",
                "exhausted": False,
            })
        return {"sent": False, "queued": True, "detail": "ordered_after_pending"}

    sent, detail = _send_feishu(db, text)
    if sent or detail == "disabled":
        return {"sent": sent, "queued": False, "detail": detail}

    if not any(q.get("dedupe_key") == dedupe_key for q in queue):
        queue.append({
            "id": uuid4().hex,
            "dedupe_key": dedupe_key,
            "text": text,
            "attempts": 1,
            "next_at": (now + timedelta(minutes=30)).isoformat(),
            "created_at": now.isoformat(),
            "last_error": detail,
            "exhausted": False,
        })
    return {"sent": False, "queued": True, "detail": detail}


def _entry_for_day(state: dict, pipeline: str, day: str) -> dict:
    pipelines = state.setdefault("pipelines", {})
    entry = pipelines.get(pipeline)
    if not isinstance(entry, dict) or entry.get("date") != day:
        entry = {
            "date": day,
            "failures": 0,
            "success": False,
            "final": False,
            "last_error": None,
            "last_attempt_at": None,
            "next_retry_at": None,
            "notifications": [],
        }
        pipelines[pipeline] = entry
    return entry


def get_pipeline(db: Session, pipeline: str, *, now: Optional[datetime] = None) -> dict:
    current = _now(now)
    state = _load(db)
    return dict(_entry_for_day(state, pipeline, current.date().isoformat()))


def needs_retry(db: Session, pipeline: str, *, now: Optional[datetime] = None) -> bool:
    entry = get_pipeline(db, pipeline, now=now)
    return bool(entry.get("failures")) and not entry.get("success") and not entry.get("final")


def record_failure(
    db: Session,
    pipeline: str,
    error: str,
    *,
    retry_slots: Iterable[datetime],
    now: Optional[datetime] = None,
    max_failures: int = 4,
) -> dict:
    """记录一次失败并立即发飞书；最后一次明确写“今日失败”。"""
    if pipeline not in PIPELINE_LABELS:
        raise ValueError(f"未知关键自动化: {pipeline}")
    current = _now(now)
    state = _load(db)
    day = current.date().isoformat()
    entry = _entry_for_day(state, pipeline, day)
    if entry.get("success") or entry.get("final"):
        return {
            "pipeline": pipeline,
            "ignored": "already_closed",
            "failures": int(entry.get("failures") or 0),
            "final": bool(entry.get("final")),
        }

    failures = int(entry.get("failures") or 0) + 1
    future_slots = sorted(_now(x) for x in retry_slots if _now(x) > current)
    next_retry = future_slots[0] if future_slots and failures < max_failures else None
    final = failures >= max_failures or next_retry is None
    safe_error = _safe_error(error)
    label = PIPELINE_LABELS[pipeline]

    if final:
        text = (
            f"❌ {day}【{label}】今日失败\n"
            f"已连续执行失败 {failures} 次，今天不再自动重试。\n"
            f"最后原因：{safe_error}\n"
            "请人工检查；明天仍会按正常时间重新开始。"
        )
        event = "final"
    else:
        minutes = max(1, int((next_retry - current).total_seconds() // 60))
        delay = (
            f"{minutes // 60}小时"
            if minutes >= 60 and minutes % 60 == 0
            else f"{minutes}分钟"
        )
        text = (
            f"⚠️ {day}【{label}】第{failures}次执行失败\n"
            f"原因：{safe_error}\n"
            f"系统将在 {next_retry.strftime('%H:%M')}（约{delay}后）自动进行第{failures + 1}次尝试。"
        )
        event = f"failure-{failures}"

    delivery = _deliver_or_queue(
        db,
        state,
        dedupe_key=f"{pipeline}:{day}:{event}",
        text=text,
        now=current,
    )
    entry.update({
        "failures": failures,
        "success": False,
        "final": final,
        "last_error": safe_error,
        "last_attempt_at": current.isoformat(),
        "next_retry_at": next_retry.isoformat() if next_retry else None,
    })
    entry.setdefault("notifications", []).append({
        "event": event,
        "at": current.isoformat(),
        "sent": delivery["sent"],
        "queued": delivery["queued"],
    })
    _save(db, state)
    return {
        "pipeline": pipeline,
        "failures": failures,
        "final": final,
        "next_retry_at": entry["next_retry_at"],
        "notification": delivery,
    }


def record_success(
    db: Session,
    pipeline: str,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """标记今日成功；只有此前失败过才发一条恢复通知。"""
    if pipeline not in PIPELINE_LABELS:
        raise ValueError(f"未知关键自动化: {pipeline}")
    current = _now(now)
    state = _load(db)
    day = current.date().isoformat()
    entry = _entry_for_day(state, pipeline, day)
    if entry.get("success"):
        return {"pipeline": pipeline, "already_success": True, "recovered": False}

    failures = int(entry.get("failures") or 0)
    delivery = {"sent": False, "queued": False, "detail": "normal_success"}
    recovered = failures > 0
    if recovered:
        label = PIPELINE_LABELS[pipeline]
        text = (
            f"✅ {day}【{label}】自动重试成功\n"
            f"此前失败 {failures} 次，本次已恢复，今天不再重试。"
        )
        delivery = _deliver_or_queue(
            db,
            state,
            dedupe_key=f"{pipeline}:{day}:recovered",
            text=text,
            now=current,
        )

    entry.update({
        "success": True,
        "final": False,
        "last_attempt_at": current.isoformat(),
        "next_retry_at": None,
    })
    if recovered:
        entry.setdefault("notifications", []).append({
            "event": "recovered",
            "at": current.isoformat(),
            "sent": delivery["sent"],
            "queued": delivery["queued"],
        })
    _save(db, state)
    return {
        "pipeline": pipeline,
        "recovered": recovered,
        "failures": failures,
        "notification": delivery,
    }


def finalize_open_failures(
    db: Session, *, now: Optional[datetime] = None,
) -> dict:
    """晚间兜底：若重试班次因重启等原因漏跑，仍发送“今日失败”收口。"""
    current = _now(now)
    state = _load(db)
    day = current.date().isoformat()
    finalized: list[str] = []
    for pipeline, label in PIPELINE_LABELS.items():
        entry = _entry_for_day(state, pipeline, day)
        failures = int(entry.get("failures") or 0)
        if failures <= 0 or entry.get("success") or entry.get("final"):
            continue
        safe_error = _safe_error(entry.get("last_error") or "重试班次未能完成")
        text = (
            f"❌ {day}【{label}】今日失败\n"
            f"今天已失败 {failures} 次，晚间重试窗口已经结束。\n"
            f"最后原因：{safe_error}\n"
            "请人工检查；明天仍会按正常时间重新开始。"
        )
        delivery = _deliver_or_queue(
            db,
            state,
            dedupe_key=f"{pipeline}:{day}:finalizer",
            text=text,
            now=current,
        )
        entry.update({
            "final": True,
            "next_retry_at": None,
            "last_attempt_at": current.isoformat(),
        })
        entry.setdefault("notifications", []).append({
            "event": "finalizer",
            "at": current.isoformat(),
            "sent": delivery["sent"],
            "queued": delivery["queued"],
        })
        finalized.append(pipeline)
    _save(db, state)
    return {"finalized": finalized}


def retry_pending_notifications(
    db: Session, *, now: Optional[datetime] = None,
) -> dict:
    """重发飞书自身发送失败的关键告警，最多 8 次，避免无限循环。"""
    current = _now(now)
    state = _load(db)
    queue = state.setdefault("notification_queue", [])
    retried = sent = exhausted = 0
    for item in queue:
        if item.get("sent_at") or item.get("exhausted"):
            continue
        try:
            next_at = datetime.fromisoformat(str(item.get("next_at") or ""))
            next_at = _now(next_at)
        except (TypeError, ValueError):
            next_at = current
        if next_at > current:
            continue
        attempts = int(item.get("attempts") or 0)
        if attempts >= _MAX_NOTIFY_ATTEMPTS:
            item["exhausted"] = True
            exhausted += 1
            continue
        retried += 1
        ok, detail = _send_feishu(db, str(item.get("text") or ""))
        attempts += 1
        item["attempts"] = attempts
        item["last_error"] = detail
        if ok:
            item["sent_at"] = current.isoformat()
            sent += 1
        elif detail != "disabled":
            delay_minutes = min(30 * (2 ** max(0, attempts - 1)), 360)
            item["next_at"] = (current + timedelta(minutes=delay_minutes)).isoformat()
            if attempts >= _MAX_NOTIFY_ATTEMPTS:
                item["exhausted"] = True
                exhausted += 1
    _save(db, state)
    return {"retried": retried, "sent": sent, "exhausted": exhausted}

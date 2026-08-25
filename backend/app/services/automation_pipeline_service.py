"""关键自动化的逐次失败告警、有限重试状态和微信 Push 必达队列。

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
_DESCRIPTION = "关键自动化失败/重试/最终失败状态、程序维护分流及微信Push待发送队列"
_MAX_NOTIFY_ATTEMPTS = 8

_PROGRAM_FAILURE_PATTERNS = (
    "attributeerror",
    "keyerror",
    "typeerror",
    "nameerror",
    "importerror",
    "modulenotfounderror",
    "syntaxerror",
    "selector not found",
    "locator not found",
    "字段缺失",
    "字段不存在",
    "选择器失效",
    "程序异常",
    "代码异常",
    "数据契约",
    "schema mismatch",
    "contract mismatch",
)

_EXECUTION_FAILURE_PATTERNS = (
    "timeouterror",
    "timeout",
    "超时",
    "connectionerror",
    "连接失败",
    "连接中断",
    "网络异常",
    "pc离线",
    "web-agent离线",
    "503",
    "502",
    "429",
    "rate limit",
    "限流",
    "验证码",
    "登录失效",
    "待口令",
    "密码",
)

PIPELINE_LABELS = {
    "order_delivery": "订单自动推送",
    "balance_pull": "账户余额自动拉取",
    "flow_pull": "收支流水自动拉取",
}


def _now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now().astimezone()
    return current if current.tzinfo is not None else current.astimezone()


def _default() -> dict:
    return {
        "pipelines": {},
        "notification_queue": [],
        "program_maintenance_queue": [],
    }


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
    state.setdefault("program_maintenance_queue", [])
    if not isinstance(state["pipelines"], dict):
        state["pipelines"] = {}
    if not isinstance(state["notification_queue"], list):
        state["notification_queue"] = []
    if not isinstance(state["program_maintenance_queue"], list):
        state["program_maintenance_queue"] = []
    return state


def _save(db: Session, state: dict) -> None:
    # 只保留最近 100 条待发/已耗尽记录，防配置无限膨胀。
    state["notification_queue"] = state.get("notification_queue", [])[-100:]
    state["program_maintenance_queue"] = state.get(
        "program_maintenance_queue", []
    )[-100:]
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


def classify_failure(error: str) -> dict[str, str]:
    """把自动化故障分给执行端或程序维护端。

    只有具有确定代码/契约特征的错误才停止业务重试并进入程序维护队列；
    平台、网络、超时和交互门继续由执行端按既定时刻有限重试。未知错误
    保守地留在执行端重试，同时标记为需要复核，避免分类器本身阻断业务。
    """
    normalized = str(error or "").lower()
    if any(pattern in normalized for pattern in _PROGRAM_FAILURE_PATTERNS):
        return {
            "owner": "program_maintenance",
            "label": "程序修复",
            "reason": "deterministic_program_error",
            "retry_policy": "stop_and_review",
        }
    if any(pattern in normalized for pattern in _EXECUTION_FAILURE_PATTERNS):
        return {
            "owner": "execution",
            "label": "执行端",
            "reason": "transient_or_interaction_error",
            "retry_policy": "scheduled_retry",
        }
    return {
        "owner": "execution",
        "label": "执行端（待复核）",
        "reason": "unclassified_error",
        "retry_policy": "scheduled_retry",
    }


def _queue_program_maintenance(
    state: dict,
    *,
    pipeline: str,
    day: str,
    error: str,
    routing: dict[str, str],
    current: datetime,
) -> dict:
    """登记去重、可审计的程序维护项，不触发外部业务动作。"""
    queue = state.setdefault("program_maintenance_queue", [])
    dedupe_key = f"{pipeline}:{day}:{routing['reason']}"
    for item in reversed(queue):
        if item.get("dedupe_key") == dedupe_key and item.get("status") == "open":
            item["last_seen_at"] = current.isoformat()
            item["occurrences"] = int(item.get("occurrences") or 1) + 1
            item["last_error"] = error
            return item
    item = {
        "id": uuid4().hex,
        "dedupe_key": dedupe_key,
        "pipeline": pipeline,
        "date": day,
        "owner": routing["owner"],
        "reason": routing["reason"],
        "retry_policy": routing["retry_policy"],
        "status": "open",
        "created_at": current.isoformat(),
        "last_seen_at": current.isoformat(),
        "occurrences": 1,
        "last_error": error,
    }
    queue.append(item)
    return item


def _resolve_program_maintenance(
    state: dict, pipeline: str, day: str, current: datetime,
) -> int:
    resolved = 0
    for item in state.setdefault("program_maintenance_queue", []):
        if (
            item.get("pipeline") == pipeline
            and item.get("date") == day
            and item.get("status") == "open"
        ):
            item["status"] = "resolved"
            item["resolved_at"] = current.isoformat()
            resolved += 1
    return resolved


def _send_feishu(db: Session, text: str) -> tuple[bool, str]:
    """兼容旧测试/队列入口：关键自动化文字统一发微信 Push，不进飞书订单群。"""
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return False, "disabled"
    try:
        from app.services import notify_service

        return notify_service.notify(
            db,
            text,
            level="warn",
            title="畔色 ERP | 自动化状态",
            wechat_allowed=True,
            enqueue_on_failure=False,
        )
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
            "stages": [],
        }
        pipelines[pipeline] = entry
    return entry


def record_stage(
    db: Session,
    pipeline: str,
    stage: str,
    *,
    status: str = "ok",
    detail: str | None = None,
    artifacts: Iterable[str] | None = None,
    now: Optional[datetime] = None,
) -> dict:
    """Persist one evidence checkpoint without sending a notification."""
    if pipeline not in PIPELINE_LABELS:
        raise ValueError(f"未知关键自动化: {pipeline}")
    current = _now(now)
    state = _load(db)
    entry = _entry_for_day(state, pipeline, current.date().isoformat())
    event = {
        "stage": str(stage)[:80],
        "status": str(status)[:40],
        "at": current.isoformat(),
        "detail": _safe_error(detail or "") if detail else None,
        "artifacts": [str(item)[:300] for item in (artifacts or [])][:20],
    }
    stages = entry.setdefault("stages", [])
    stages.append(event)
    entry["stages"] = stages[-60:]
    entry["last_stage"] = event
    _save(db, state)
    return event


def get_pipeline(db: Session, pipeline: str, *, now: Optional[datetime] = None) -> dict:
    current = _now(now)
    state = _load(db)
    return dict(_entry_for_day(state, pipeline, current.date().isoformat()))


def list_program_maintenance(
    db: Session, *, status: str | None = "open", limit: int = 100,
) -> list[dict]:
    """供程序维护任务只读领取结构化故障；不触发任务、通知或业务重跑。"""
    state = _load(db)
    items = state.get("program_maintenance_queue", [])
    if status is not None:
        items = [item for item in items if item.get("status") == status]
    bounded_limit = max(1, min(int(limit), 100))
    return [dict(item) for item in items[-bounded_limit:]][::-1]


def begin_run(
    db: Session,
    pipeline: str,
    *,
    run_key: str,
    now: Optional[datetime] = None,
) -> dict:
    """Open one scheduled business run without erasing its audit history.

    A manual report/password recovery can pause or even close ``order_delivery``
    before the regular 18:00 pull starts.  The daily pull is a new business run
    and must not inherit that stale terminal flag.  ``run_key`` makes this
    transition idempotent: retries of the same run keep their failure counter,
    while a genuinely new run clears only the current terminal/error fields.
    Stages, notifications and failure counts remain available for audit.
    """
    if pipeline not in PIPELINE_LABELS:
        raise ValueError(f"未知关键自动化: {pipeline}")
    normalized_key = str(run_key or "").strip()
    if not normalized_key:
        raise ValueError("run_key 不能为空")
    current = _now(now)
    state = _load(db)
    entry = _entry_for_day(state, pipeline, current.date().isoformat())
    if entry.get("active_run_key") != normalized_key:
        entry.update({
            "active_run_key": normalized_key,
            "run_started_at": current.isoformat(),
            "success": False,
            "final": False,
            "waiting_input": False,
            "last_error": None,
            "last_attempt_at": None,
            "next_retry_at": None,
            "failure_owner": None,
            "failure_reason": None,
            "retry_policy": None,
            "maintenance_item_id": None,
        })
        _save(db, state)
        return {**dict(entry), "opened": True}
    return {**dict(entry), "opened": False}


def needs_retry(db: Session, pipeline: str, *, now: Optional[datetime] = None) -> bool:
    entry = get_pipeline(db, pipeline, now=now)
    return bool(entry.get("failures")) and not entry.get("success") and not entry.get("final")


def resume_for_retry(
    db: Session,
    pipeline: str,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """Reopen a chain after new human input made another attempt meaningful."""
    if pipeline not in PIPELINE_LABELS:
        raise ValueError(f"未知关键自动化: {pipeline}")
    current = _now(now)
    state = _load(db)
    entry = _entry_for_day(state, pipeline, current.date().isoformat())
    entry.update({
        "success": False,
        "final": False,
        "waiting_input": False,
        "last_error": None,
        "next_retry_at": None,
        "failure_owner": None,
        "failure_reason": None,
        "retry_policy": None,
        "maintenance_item_id": None,
    })
    _save(db, state)
    return dict(entry)


def pause_for_input(
    db: Session,
    pipeline: str,
    reason: str,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """暂停没有新输入就不可能成功的重试。

    典型场景是用户已经给了发货报表口令，但该口令与当前加密文件不匹配。
    此时继续按小时重试同一个口令只会制造重复告警；保留明确原因，等新口令到达后
    由业务回调立即重试，成功时 ``record_success`` 会重新打开并正式销账。
    """
    if pipeline not in PIPELINE_LABELS:
        raise ValueError(f"未知关键自动化: {pipeline}")
    current = _now(now)
    state = _load(db)
    day = current.date().isoformat()
    entry = _entry_for_day(state, pipeline, day)
    entry.update({
        "success": False,
        "final": True,
        "waiting_input": True,
        "last_error": _safe_error(reason),
        "last_attempt_at": current.isoformat(),
        "next_retry_at": None,
        "failure_owner": "execution",
        "failure_reason": "waiting_for_user_input",
        "retry_policy": "wait_for_input",
        "maintenance_item_id": None,
    })
    superseded = _supersede_pending_failure_notifications(
        state, pipeline, day, current,
    )
    _save(db, state)
    return {
        "pipeline": pipeline,
        "paused": True,
        "reason": entry["last_error"],
        "superseded_notifications": superseded,
    }


def _supersede_pending_failure_notifications(
    state: dict, pipeline: str, day: str, current: datetime,
) -> int:
    """成功后取消尚未送达的旧失败通知，避免恢复后再冒出过期重试提醒。"""
    prefix = f"{pipeline}:{day}:"
    superseded = 0
    for item in state.setdefault("notification_queue", []):
        if not str(item.get("dedupe_key") or "").startswith(prefix):
            continue
        if item.get("sent_at") or item.get("exhausted"):
            continue
        item["exhausted"] = True
        item["superseded_at"] = current.isoformat()
        item["last_error"] = "pipeline_recovered_before_delivery"
        superseded += 1
    return superseded


def record_failure(
    db: Session,
    pipeline: str,
    error: str,
    *,
    retry_slots: Iterable[datetime],
    now: Optional[datetime] = None,
    max_failures: int = 4,
) -> dict:
    """记录一次失败、确定处理归属并立即发微信 Push。

    执行类故障保留既定有限重试；确定性程序故障停止业务盲重试，登记到
    ``program_maintenance_queue``，等待程序修复后再由正常调度/明确补跑恢复。
    """
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
    safe_error = _safe_error(error)
    routing = classify_failure(safe_error)
    future_slots = sorted(_now(x) for x in retry_slots if _now(x) > current)
    program_failure = routing["owner"] == "program_maintenance"
    next_retry = (
        future_slots[0]
        if not program_failure and future_slots and failures < max_failures
        else None
    )
    final = program_failure or failures >= max_failures or next_retry is None
    label = PIPELINE_LABELS[pipeline]

    maintenance_item = None
    if program_failure:
        maintenance_item = _queue_program_maintenance(
            state,
            pipeline=pipeline,
            day=day,
            error=safe_error,
            routing=routing,
            current=current,
        )

    if program_failure:
        text = (
            f"❌ {day}【{label}】检测到程序问题\n"
            f"原因：{safe_error}\n"
            "处理归属：程序修复（已登记维护队列，停止业务盲重试）。\n"
            "修复完成后再由正常计划或明确批准的补跑恢复。"
        )
        event = "program-maintenance"
    elif final:
        text = (
            f"❌ {day}【{label}】今日失败\n"
            f"已连续执行失败 {failures} 次，今天不再自动重试。\n"
            f"最后原因：{safe_error}\n"
            "处理归属：执行端（请检查平台、网络或交互门）；明天仍会按正常时间重新开始。"
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
            f"处理归属：{routing['label']}。\n"
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
        "waiting_input": False,
        "last_error": safe_error,
        "last_attempt_at": current.isoformat(),
        "next_retry_at": next_retry.isoformat() if next_retry else None,
        "failure_owner": routing["owner"],
        "failure_reason": routing["reason"],
        "retry_policy": routing["retry_policy"],
        "maintenance_item_id": (
            maintenance_item.get("id") if maintenance_item else None
        ),
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
        "routing": routing,
        "maintenance_item_id": entry["maintenance_item_id"],
        "notification": delivery,
    }


def record_success(
    db: Session,
    pipeline: str,
    *,
    now: Optional[datetime] = None,
    success_detail: str | None = None,
) -> dict:
    """标记今日成功；只有此前失败过才发一条恢复通知。"""
    if pipeline not in PIPELINE_LABELS:
        raise ValueError(f"未知关键自动化: {pipeline}")
    current = _now(now)
    state = _load(db)
    day = current.date().isoformat()
    entry = _entry_for_day(state, pipeline, day)
    if entry.get("success"):
        resolved_maintenance = _resolve_program_maintenance(
            state, pipeline, day, current,
        )
        superseded = _supersede_pending_failure_notifications(
            state, pipeline, day, current,
        )
        # A previous failure reason must not remain visible after durable
        # success evidence exists.  This also repairs older rows where the
        # success flag was set but last_error still described a stale failure.
        entry.update({
            "last_error": None,
            "final": False,
            "waiting_input": False,
            "next_retry_at": None,
        })
        _save(db, state)
        return {
            "pipeline": pipeline,
            "already_success": True,
            "recovered": False,
            "superseded_notifications": superseded,
            "resolved_maintenance": resolved_maintenance,
        }

    failures = int(entry.get("failures") or 0)
    superseded = _supersede_pending_failure_notifications(
        state, pipeline, day, current,
    )
    resolved_maintenance = _resolve_program_maintenance(
        state, pipeline, day, current,
    )
    delivery = {"sent": False, "queued": False, "detail": "normal_success"}
    recovered = failures > 0
    if recovered:
        label = PIPELINE_LABELS[pipeline]
        text = (
            f"✅ {day}【{label}】自动重试成功\n"
            f"此前失败 {failures} 次，本次已恢复，今天不再重试。"
        )
        if success_detail:
            text += f"\n结果：{_safe_error(success_detail)}"
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
        "waiting_input": False,
        "last_error": None,
        "last_attempt_at": current.isoformat(),
        "next_retry_at": None,
        "failure_owner": None,
        "failure_reason": None,
        "retry_policy": None,
        "maintenance_item_id": None,
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
        "superseded_notifications": superseded,
        "resolved_maintenance": resolved_maintenance,
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
            "处理归属：执行端（请检查平台、网络或交互门）；明天仍会按正常时间重新开始。"
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
            "failure_owner": entry.get("failure_owner") or "execution",
            "failure_reason": entry.get("failure_reason") or "retry_window_ended",
            "retry_policy": "next_scheduled_day",
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

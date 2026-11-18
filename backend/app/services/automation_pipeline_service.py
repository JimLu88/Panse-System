"""关键自动化的失败记录、有限重试与一次性群提醒。

只覆盖用户明确指定的三条链路：

* 订单自动推送
* 账户余额自动拉取
* 收支流水自动拉取

每个自然日独立计数。每项任务当天只发一次失败消息，后续尝试、收口和恢复均保留在 ERP。
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
    "order_export_interrupted",
    "order_export_unknown",
    "order_export_partial",
    "contract mismatch",
    "deterministic:",
    "wechat_bill_table_missing",
    "wechat_bill_download_missing",
    "download.save_as: target page, context or browser has been closed",
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
    订单登录失效等待新的登录回执；网络和超时由执行端有限重试。未知错误
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
    if any(pattern in normalized for pattern in (
        "session_expired", "登录已失效", "登录失效", "登录状态已失效",
        "登录态缺失", "需重新登录", "需要重新登录", "需人工登录", "需扫码",
    )):
        return {
            "owner": "execution", "label": "等待用户扫码登录",
            "reason": "login_required", "retry_policy": "wait_for_input",
        }
    if any(pattern in normalized for pattern in (
        "本sku专属尺寸未核实", "主订单汇总被作为子订单发送",
    )):
        return {
            "owner": "execution",
            "label": "订单资料与历史凭证核对",
            "reason": "order_evidence_review_required",
            "retry_policy": "wait_for_input",
        }
    # Bare substrings also match long order/SKU identifiers (e.g. ...5037339).
    if (any(pattern in normalized for pattern in _EXECUTION_FAILURE_PATTERNS)
            or re.search(r"(?<![\w])(?:503|502|429)(?![\w])", normalized)):
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
            enqueue_on_failure=False, require_receipt=True,
        )
    except Exception as exc:  # Delivery is uncertain; never replay this notice.
        return False, f"{type(exc).__name__}: {exc}"[:300]


def _short_cause(error: str) -> str:
    """Translate confirmed symptoms only; never forward tracebacks or account data."""
    low = error.lower()
    if "export_unknown" in low or "已点击导出但无确证文件" in error:
        return "已点导出，尚未拿到文件，请核对平台导出记录"
    if any(x in error for x in ("授权失效", "需授权", "需要授权", "授权过期")):
        return "需要重新授权"
    if any(x in low for x in ("扫码", "session_expired", "登录失效", "登录已失效", "需人工登录", "需要重新登录")):
        return "等待扫码登录"
    if "口令" in error and any(x in error for x in ("不匹配", "解密失败", "无法解密")):
        return "口令和当前报表不匹配，请按该报表的提示提供新口令"
    if "口令" in error:
        return "发货报表等待对应口令"
    if "离线" in error or "connection refused" in low:
        return "无法连接取数电脑，请检查取数程序"
    if "timeout" in low or "超时" in error:
        return "等待平台响应超时"
    if "wechat_bill_table_missing" in low:
        return "在微信账单页找不到要下载的账单表"
    if "target page, context or browser has been closed" in low:
        return "下载时浏览器页面已经关闭"
    if classify_failure(error)["owner"] == "program_maintenance":
        return "取数程序出错，已登记程序维护"
    if "未核实" in error or "作为子订单" in error:
        return "订单资料或工厂凭证需要核对"
    if "未配置" in error:
        return "对应账户尚未配置"
    if "未更新" in error or "成功标记" in error:
        return "还没有当天数据的更新记录"
    return "未取得有效结果，具体原因待核查"


def failure_summary(pipeline: str, error: str) -> str:
    sources = {"wechat_bill": "微信账单", "wanshifu": "万师傅", "alipay_main": "支付宝主力号",
               "wanxiangtai": "万相台", "bal_alipay_main": "支付宝主力号", "bal_wanshifu": "万师傅",
               "bal_ads": "推广账户", "bal_taobao_aggregate": "淘宝聚合账户"}
    parts = []
    for match in re.finditer(r"(\w+)流水未完成[:：]\s*([^；;]+)|\b(bal_[a-z_]+)\(([^)]*)\)", error):
        key, detail = (match[1], match[2]) if match[1] else (match[3], match[4])
        # Chinese prose is not an account identifier.
        label = sources.get(key, "流水来源")
        item = f"{label}：{_short_cause(detail)}"
        if item not in parts:
            parts.append(item)
    if "企业号流水" in error:
        parts.append("支付宝企业号：" + _short_cause(error.split("企业号流水", 1)[1].split("；")[0]))
    reason = "；".join(parts) if parts else _short_cause(error)
    action = " 请在提醒群回复“扫码”，选择对应平台。" if "等待扫码登录" in reason else ""
    return f"{PIPELINE_LABELS[pipeline]}未完成。{reason}。{action}"


def _claim_failure_notice(db: Session, pipeline: str, day: str) -> bool:
    """One durable slot per pipeline. Commit the claim before the network call."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from app.models.settings import SystemSetting

    bind = db.get_bind()
    insert = pg_insert if bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(SystemSetting).values(key="collection_notice_day:" + pipeline, value_plain=day,
                                             is_secret=False)
    statement = statement.on_conflict_do_update(index_elements=[SystemSetting.key],
        set_={"value_plain": day}, where=SystemSetting.value_plain < day)
    if bind.dialect.name == "sqlite":
        # Local SQLite has a single writer. Keep test/in-memory writes in its transaction.
        return db.execute(statement).rowcount == 1
    with bind.begin() as conn:
        return conn.execute(statement).rowcount == 1


def _notify_failure_once(db, entry, pipeline, day, error):
    # Honor receipts made before this policy was deployed, including ambiguous delivery.
    if not _claim_failure_notice(db, pipeline, day) or entry.get("notifications"):
        return {"sent": False, "queued": False, "detail": "already_reported"}
    sent, detail = _send_feishu(db, failure_summary(pipeline, error))
    return {"sent": sent, "queued": False, "detail": detail}


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


def resume_order_after_login(db: Session, *, now: Optional[datetime] = None) -> bool:
    """Consume a new shop-login receipt once; the scheduler still owns export.

    A saved session only permits another attempt, never proves fresh order data.
    Other platforms, old receipts and ambiguous login results cannot reopen it.
    """
    from sqlalchemy import select
    from app.models.settings import SystemSetting
    from app.services.platform_scan_service import ACTIVE_KEY

    # Serialize the receipt claim across workers before any business dispatch.
    db.execute(select(SystemSetting).where(
        SystemSetting.key == SETTING_KEY,
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    current = _now(now)
    state = _load(db)
    entry = _entry_for_day(state, "order_delivery", current.date().isoformat())
    if (entry.get("success") or
            classify_failure(entry.get("last_error") or "")["reason"] != "login_required"):
        return False
    row = db.execute(select(SystemSetting).where(SystemSetting.key == ACTIVE_KEY)).scalar_one_or_none()
    try:
        receipt = json.loads(row.value_plain or "{}") if row else {}
        attempted = datetime.fromisoformat(entry["last_attempt_at"])
        saved = row.updated_at if row else None
        # SQLite timestamps are UTC without tzinfo; production Postgres is aware.
        from datetime import timezone
        if saved and saved.tzinfo is None:
            saved = saved.replace(tzinfo=timezone.utc)
        if (receipt.get("state") != "success" or receipt.get("platform") != "shop"
                or not receipt.get("job") or not receipt.get("request_key")
                or receipt["job"] == entry.get("consumed_login_job")
                or not saved or saved <= _now(attempted) or saved > current
                or saved.astimezone(current.tzinfo).date() != current.date()):
            return False
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
    entry.update({"final": False, "waiting_input": False,
                  "consumed_login_job": receipt["job"],
                  "login_resumed_at": current.isoformat(), "next_retry_at": None})
    _save(db, state)
    db.commit()  # durable one-time claim before the scheduler contacts the agent
    return True


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
    """记录每次失败、确定处理归属，当天第一次向提醒群说明原因。

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
    routing = classify_failure(error)
    if routing["reason"] == "login_required" and pipeline != "order_delivery":
        # Finance has multiple independent sources. Its caller decides whether
        # all remaining sources await login; one login must not pause the rest.
        routing = {"owner": "execution", "label": "执行端",
                   "reason": "transient_or_interaction_error", "retry_policy": "scheduled_retry"}
    future_slots = sorted(_now(x) for x in retry_slots if _now(x) > current)
    program_failure = routing["owner"] == "program_maintenance"
    evidence_blocked = routing["retry_policy"] == "wait_for_input"
    next_retry = (
        future_slots[0]
        if not program_failure and not evidence_blocked and future_slots and failures < max_failures
        else None
    )
    final = program_failure or evidence_blocked or failures >= max_failures or next_retry is None

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

    event = ("program-maintenance" if program_failure else
             "login-required" if routing["reason"] == "login_required" else
             "evidence-review" if evidence_blocked else "final" if final else f"failure-{failures}")
    delivery = _notify_failure_once(db, entry, pipeline, day, error)
    entry.update({
        "failures": failures,
        "success": False,
        "final": final,
        "waiting_input": evidence_blocked,
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
        "detail": delivery["detail"],
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
    """恢复结果留在 ERP，关闭此前的维护项与旧版待发通知。"""
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
        delivery["detail"] = "recovered_in_erp"

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
            "result": _safe_error(success_detail) if success_detail else None,
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
    """晚间关闭未完成链路；只在当天还未提醒时发出一次。"""
    current = _now(now)
    state = _load(db)
    day = current.date().isoformat()
    finalized: list[str] = []
    for pipeline in PIPELINE_LABELS:
        entry = _entry_for_day(state, pipeline, day)
        failures = int(entry.get("failures") or 0)
        if failures <= 0 or entry.get("success") or entry.get("final"):
            continue
        last_stage = entry.get("last_stage") or {}
        safe_error = _safe_error(entry.get("last_error") or (
            last_stage.get("detail") if last_stage.get("status") == "fail" else None
        ) or "重试班次未能完成")
        delivery = _notify_failure_once(db, entry, pipeline, day, safe_error)
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
    """Retire unsent entries created by the old per-attempt policy, keeping their history."""
    state = _load(db)
    retired = 0
    for item in state["notification_queue"]:
        if not item.get("sent_at") and not item.get("exhausted"):
            item.update(exhausted=True, superseded_at=_now(now).isoformat(),
                        last_error="retired_per_attempt_notification")
            retired += 1
    if retired:
        _save(db, state)
    return {"retried": 0, "sent": 0, "exhausted": retired}

"""告警 / 通知中心服务 (Phase 1B).

业务需求 4/5/6/8/9/11: 缺货 / 缺快递号 / 退款待处理 / 滞销 / 退货待确认 — 统一进 Alert 表,
前端 NotificationBell 拉 GET /api/alerts/active.

设计原则:
    - dedupe_key 去重: 同种告警同对象 active 时复用, 不重复创建
    - severity=critical 自动推企业微信/钉钉 (notify_service), 但只推一次 (notified_at)
    - sticky=True: 用户不能 dismiss, 必须解决根因; 配合定时任务每 30 分钟自动 re-check 并 resolve
    - auto_resolve_until 到期自动 resolve (用于临时提醒)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.alert import Alert

_logger = logging.getLogger("panse.alert")


def upsert(
    db: Session,
    *,
    kind: str,
    severity: str,
    title: str,
    body: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    related_url: Optional[str] = None,
    context: Optional[dict] = None,
    sticky: bool = False,
    auto_resolve_after_minutes: Optional[int] = None,
    push_notify: bool = True,
) -> Alert:
    """创建或复用 active alert.

    dedupe_key 不空时, 找一条同 dedupe_key 且未 resolved 的 alert 复用 (只更新 body/context);
    否则新建一条。

    severity=critical 且未 notified 时, 自动推企业微信/钉钉群 (notify_service).
    """
    assert severity in ("info", "warn", "critical"), severity

    existing: Optional[Alert] = None
    if dedupe_key:
        existing = db.execute(
            select(Alert).where(
                Alert.dedupe_key == dedupe_key,
                Alert.resolved_at.is_(None),
            ).order_by(Alert.id.desc()).limit(1)
        ).scalar_one_or_none()

    if existing is not None:
        # 仅刷新 body / context / severity (允许升级)
        existing.body = body
        existing.context_json = context
        if severity == "critical" and existing.severity != "critical":
            existing.severity = "critical"
        if auto_resolve_after_minutes:
            existing.auto_resolve_until = datetime.now(timezone.utc) + timedelta(
                minutes=auto_resolve_after_minutes,
            )
        db.flush()
        alert = existing
    else:
        alert = Alert(
            kind=kind, severity=severity, title=title, body=body,
            dedupe_key=dedupe_key, related_url=related_url,
            context_json=context, sticky=sticky,
            auto_resolve_until=(
                datetime.now(timezone.utc) + timedelta(minutes=auto_resolve_after_minutes)
                if auto_resolve_after_minutes else None
            ),
        )
        db.add(alert)
        db.flush()

    # critical 推群 (一次); 同键 12h 冷却 = 一天最多两次 (用户拍板 2026-06-11); 总开关+白名单可配 (F9)
    if (push_notify and severity == "critical" and alert.notified_at is None
            and not (dedupe_key and _recently_notified(db, dedupe_key))
            and _push_allowed(db, kind)):
        try:
            from app.services import notify_service
            text = f"{title}\n{body or ''}".strip()
            ok, _ = notify_service.notify(
                db, text, level="error",
                title=f"畔色 ERP · {KIND_LABELS.get(kind, kind)}",
            )
            if ok:
                alert.notified_at = datetime.now(timezone.utc)
                db.flush()
        except Exception as e:  # pragma: no cover
            _logger.warning("alert push 失败 (不影响落库): %s", e)

    # Phase 12: 通过 SSE 推到所有在线 client (替代 30s 轮询)
    try:
        from app.services import sse_bus
        sse_bus.publish("alert.upserted", {
            "id": alert.id, "kind": alert.kind, "severity": alert.severity,
            "title": alert.title, "body": alert.body,
            "sticky": alert.sticky, "related_url": alert.related_url,
        })
    except Exception:  # pragma: no cover
        pass

    return alert


def _push_allowed(db: Session, kind: str) -> bool:
    """Plan F9: critical 飞书推送受总开关 + kind 白名单控制 (settings 可配, 默认全放行)。

    system_settings.alert_push_feishu_enabled = 0/false → 全部不推
    system_settings.alert_push_kind_whitelist = "low_stock_part,refund_pending" → 只推这些 kind
    """
    try:
        from app.services import settings_service
        enabled = settings_service.get(db, "alert_push_feishu_enabled", env_fallback=False)
        if enabled is not None and str(enabled).strip().lower() in ("0", "false", "off", "no"):
            return False
        wl = settings_service.get(db, "alert_push_kind_whitelist", env_fallback=False)
        if wl:
            kinds = [k.strip() for k in str(wl).split(",") if k.strip()]
            return kind in kinds
        return True
    except Exception:  # pragma: no cover - 配置读取失败按默认放行
        return True


# 推送标题的中文标签 (用户要求: 像 low_stock_part 这种英文要让正常人能看懂)
KIND_LABELS = {
    "low_stock_part": "配件缺货提醒",
    "missing_tracking": "缺快递单号",
    "refund_pending": "退款待处理",
    "return_pending": "退货待确认",
    "slow_moving": "滞销提醒",
    "data_freshness": "数据该更新了",
    "backup_stale": "备份过期",
    "writeoff_excess": "做平金额超警戒",
}


def _recently_notified(db: Session, dedupe_key: str, *, hours: int = 12) -> bool:
    """同 dedupe_key 在 N 小时内推送过 (含已 resolve 的旧条) → 冷却, 不重推。
    12h = 一天最多两次 (用户拍板 2026-06-11)。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    row = db.execute(
        select(Alert.id).where(
            Alert.dedupe_key == dedupe_key,
            Alert.notified_at.isnot(None),
            Alert.notified_at >= cutoff,
        ).limit(1)
    ).scalar_one_or_none()
    return row is not None


def get_active_context(db: Session, dedupe_key: str) -> Optional[dict]:
    """取同 dedupe_key 的 active alert 的 context_json (调用方合并列表时用, Plan C4)。"""
    row = db.execute(
        select(Alert).where(
            Alert.dedupe_key == dedupe_key,
            Alert.resolved_at.is_(None),
        ).order_by(Alert.id.desc()).limit(1)
    ).scalar_one_or_none()
    return row.context_json if row is not None else None


def resolve(
    db: Session, alert_id: int, *,
    resolved_by: str = "system",
) -> Optional[Alert]:
    """手动 resolve. sticky 也允许; 业务层应在调用前确认根因已修复."""
    a = db.get(Alert, alert_id)
    if a is None or a.resolved_at is not None:
        return a
    a.resolved_at = datetime.now(timezone.utc)
    a.resolved_by = resolved_by
    db.flush()
    try:
        from app.services import sse_bus
        sse_bus.publish("alert.resolved", {"id": a.id})
    except Exception:  # pragma: no cover
        pass
    return a


def resolve_by_dedupe(
    db: Session, dedupe_key: str, *, resolved_by: str = "system",
) -> int:
    """按 dedupe_key 批量 resolve (业务流程关闭时, 比如库存补足 → 缺货告警自动消失)."""
    rows = db.execute(
        select(Alert).where(
            Alert.dedupe_key == dedupe_key,
            Alert.resolved_at.is_(None),
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for a in rows:
        a.resolved_at = now
        a.resolved_by = resolved_by
    db.flush()
    return len(rows)


def list_active(
    db: Session, *,
    severity: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 200,
) -> list[Alert]:
    """未 resolved + 未 auto_resolve 过期 的告警."""
    now = datetime.now(timezone.utc)
    q = select(Alert).where(
        Alert.resolved_at.is_(None),
        or_(Alert.auto_resolve_until.is_(None),
            Alert.auto_resolve_until > now),
    ).order_by(Alert.severity.desc(), Alert.id.desc()).limit(limit)
    if severity:
        q = q.where(Alert.severity == severity)
    if kind:
        q = q.where(Alert.kind == kind)
    return list(db.execute(q).scalars())


def auto_expire(db: Session) -> int:
    """定时任务调: 标记过了 auto_resolve_until 的告警为 resolved."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(Alert).where(
            Alert.resolved_at.is_(None),
            Alert.auto_resolve_until.isnot(None),
            Alert.auto_resolve_until <= now,
        )
    ).scalars().all()
    for a in rows:
        a.resolved_at = now
        a.resolved_by = "auto_expire"
    db.flush()
    return len(rows)


def count_unresolved_by_severity(db: Session) -> dict:
    """前端 NotificationBell 角标用: {info: N, warn: N, critical: N}."""
    out = {"info": 0, "warn": 0, "critical": 0}
    rows = list_active(db, limit=1000)
    for a in rows:
        out[a.severity] = out.get(a.severity, 0) + 1
    return out

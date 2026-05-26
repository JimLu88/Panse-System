"""告警 / 通知 (Phase 1B).

业务需求 4/5/6/8/9/11: 缺货 / 缺快递单号 / 退款待处理 / 滞销 / 退货待确认 — 都生成 Alert,
前端顶部 NotificationBell 拉 GET /api/alerts/active 渲染, 高优 modal 强弹。

设计:
    - kind: 类型, 例如 'low_stock_part' / 'missing_tracking' / 'refund_pending'
    - severity: info / warn / critical (critical 会触发企业微信/钉钉群推送 + modal 强弹)
    - dedupe_key: 去重 (同种告警同对象 active 时不重复创建), 例如 'low_stock:M001'
    - related_url: 前端点击跳的页面
    - auto_resolve_until / resolved_at: 自动失效时间; 用户手动 dismiss 也 resolve
    - sticky: True 时不能 dismiss, 必须解决根因 (如未填快递号一直弹) → 持续弹窗
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # info / warn / critical

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text)

    dedupe_key: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    related_url: Mapped[Optional[str]] = mapped_column(String(256))
    # 前端点击跳的相对路由, 如 "/inventory/parts?code=M001"

    context_json: Mapped[Optional[dict]] = mapped_column(JSON)

    sticky: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # True = 用户不能 dismiss, 必须解决业务根因 (持续弹窗的依据)

    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64))
    # 用户名 / 'system' (定时任务自动 resolve) / 'auto_expire'

    auto_resolve_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # 过了这个时间自动 resolve (用于临时提醒)

    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # 已推过企业微信/钉钉的时间戳; 避免每次 tick 重推

    __table_args__ = (
        Index("ix_alerts_kind_resolved", "kind", "resolved_at"),
        Index("ix_alerts_dedupe_resolved", "dedupe_key", "resolved_at"),
    )

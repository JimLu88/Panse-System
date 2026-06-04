"""认证 / 权限 / 审计 (plan §10 Phase 6)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 角色定义（plan 没强约束角色清单，按 ERP 业务常识三档）：
ROLES = ("admin", "operator", "viewer")
ROLE_DESC = {
    "admin": "所有权限，含用户管理 / 代码补丁审批",
    "operator": "录入、修改业务数据；不能改账号 / 设置 / 审批代码补丁",
    "viewer": "只读 — 看报表、看异常、看对账，不能写",
}


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="viewer", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 强制首次登录改密 (默认 admin/admin 创建时置 True; 改密后清零)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AuditLog(Base, TimestampMixin):
    """业务写操作的审计 (plan §10 Phase 6 操作日志)。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status_code: Mapped[Optional[int]] = mapped_column()
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    request_body: Mapped[Optional[dict]] = mapped_column(JSON)
    note: Mapped[Optional[str]] = mapped_column(Text)

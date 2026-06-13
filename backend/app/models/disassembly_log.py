"""拆 BOM 历史 (用户需求 2026-06-11: 拆解留痕 + 可回撤, 防误操作)。

一行 = 一次拆解: 成品 -qty, parts_json 里每项物料 +qty。
回撤 = 反向操作 (成品 +qty, 物料 -qty), 标 undone_at/undone_by, 只能撤一次。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DisassemblyLog(Base, TimestampMixin):
    __tablename__ = "disassembly_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    sku_code: Mapped[Optional[str]] = mapped_column(String(64))
    qty: Mapped[Numeric] = mapped_column(Numeric(12, 3), nullable=False)
    parts_json: Mapped[Optional[list]] = mapped_column(JSON)   # [{material_code, qty}]
    actor: Mapped[Optional[str]] = mapped_column(String(64))
    undone_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    undone_by: Mapped[Optional[str]] = mapped_column(String(64))

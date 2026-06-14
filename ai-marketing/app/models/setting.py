"""运行时设置（key/value）：让非技术用户在界面里配置集成项，无需改 .env。

仅存非敏感的集成配置（采集器URL/飞书webhook）。API_TOKEN 仍只走 env（安全）。
"""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")

"""pricing_formula_rules — configurable formula rules for pricing computed columns."""
from __future__ import annotations
from typing import Optional
from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class PricingFormulaRule(Base, TimestampMixin):
    __tablename__ = "pricing_formula_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128))
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

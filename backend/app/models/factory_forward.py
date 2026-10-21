from datetime import datetime
from sqlalchemy import String, Integer, JSON, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class FactoryForward(Base):
    __tablename__ = 'factory_forwards'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(24), default='pending')
    revision: Mapped[int] = mapped_column(Integer, default=0)
    actor: Mapped[str | None] = mapped_column(String(160), nullable=True)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)
    image_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    card_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    card_state: Mapped[str] = mapped_column(String(24), default='none')
    sync_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_attempts: Mapped[int] = mapped_column(Integer, default=0)
    sync_error: Mapped[str | None] = mapped_column(String(160), nullable=True)

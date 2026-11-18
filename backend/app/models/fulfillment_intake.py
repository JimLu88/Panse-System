"""One source message per registration; a package and an order can be claimed only once."""
from sqlalchemy import Integer, String, JSON, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class FulfillmentIntake(Base, TimestampMixin):
    __tablename__ = "fulfillment_intakes"
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128))
    image_key: Mapped[str | None] = mapped_column(String(256))
    image_hash: Mapped[str | None] = mapped_column(String(64))
    order_no: Mapped[str | None] = mapped_column(String(64))
    sub_order_no: Mapped[str | None] = mapped_column(String(64))
    carrier: Mapped[str | None] = mapped_column(String(64))
    tracking_no: Mapped[str | None] = mapped_column(String(128))
    readbacks: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    state: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(String(256))
    order_claim: Mapped[str | None] = mapped_column(String(64), unique=True)
    parcel_claim: Mapped[str | None] = mapped_column(String(196), unique=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True)
    review_evidence: Mapped[dict | None] = mapped_column(JSON)


class FulfillmentBatch(Base, TimestampMixin):
    __tablename__ = "fulfillment_batches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), default="prepared")
    rows: Mapped[list] = mapped_column(JSON)
    file_sha256: Mapped[str] = mapped_column(String(64))
    file_bytes: Mapped[bytes] = mapped_column(LargeBinary)

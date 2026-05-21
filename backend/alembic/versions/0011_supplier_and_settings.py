"""suppliers + delivery_notes + delivery_note_lines + delivery_files + system_settings

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    # 系统设置 KV (AI provider 等)
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("value_plain", sa.Text),
        sa.Column("value_encrypted", sa.Text),
        sa.Column("is_secret", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.String(255)),
        *_ts(),
    )
    op.create_index("ix_system_settings_key", "system_settings", ["key"])

    # 供应商
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("supplier_type", sa.String(32), nullable=False, server_default="other"),
        sa.Column("contact", sa.String(128)),
        sa.Column("phone", sa.String(32)),
        sa.Column("address", sa.String(255)),
        sa.Column("payment_terms", sa.String(64)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("remark", sa.Text),
        *_ts(),
    )

    # 送货单原图归档
    op.create_table(
        "delivery_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("original_name", sa.String(255)),
        sa.Column("mime_type", sa.String(64)),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("uploaded_by", sa.String(64)),
        *_ts(),
    )
    op.create_index("ix_delivery_files_supplier", "delivery_files", ["supplier_id"])
    op.create_index(
        "ix_delivery_files_supplier_period", "delivery_files",
        ["supplier_id", "year", "month"],
    )

    # 送货单 (一张单据)
    op.create_table(
        "delivery_notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("note_no", sa.String(64)),
        sa.Column("delivery_date", sa.Date),
        sa.Column("total_amount", sa.Numeric(14, 2)),
        sa.Column("source_file_id", sa.Integer, sa.ForeignKey("delivery_files.id")),
        sa.Column("ocr_model", sa.String(64)),
        sa.Column("ocr_warnings", sa.JSON),
        sa.Column("ocr_confidence", sa.Numeric(5, 2)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending_review"),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("remark", sa.Text),
        *_ts(),
        sa.UniqueConstraint("supplier_id", "note_no", name="uq_delivery_notes_supplier_note_no"),
    )
    op.create_index("ix_delivery_notes_supplier", "delivery_notes", ["supplier_id"])
    op.create_index("ix_delivery_notes_note_no", "delivery_notes", ["note_no"])
    op.create_index("ix_delivery_notes_date", "delivery_notes", ["delivery_date"])
    op.create_index("ix_delivery_notes_status", "delivery_notes", ["status"])
    op.create_index(
        "ix_delivery_notes_supplier_date", "delivery_notes", ["supplier_id", "delivery_date"]
    )

    # 送货单明细行
    op.create_table(
        "delivery_note_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("delivery_note_id", sa.Integer, sa.ForeignKey("delivery_notes.id"), nullable=False),
        sa.Column("line_no", sa.Integer, nullable=False, server_default="1"),
        sa.Column("item_name", sa.String(255)),
        sa.Column("spec", sa.String(128)),
        sa.Column("unit", sa.String(16)),
        sa.Column("qty", sa.Numeric(12, 4), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2)),
        sa.Column("amount", sa.Numeric(14, 2)),
        sa.Column("matched_order_no", sa.String(64)),
        sa.Column("match_confidence", sa.Numeric(5, 2)),
        sa.Column("match_method", sa.String(32)),
        sa.Column("match_candidates", sa.JSON),
        sa.Column("ocr_raw_text", sa.Text),
        sa.Column("ocr_warnings", sa.JSON),
        sa.Column("remark", sa.Text),
        *_ts(),
    )
    op.create_index("ix_delivery_note_lines_note", "delivery_note_lines", ["delivery_note_id"])
    op.create_index("ix_delivery_note_lines_matched_order", "delivery_note_lines", ["matched_order_no"])


def downgrade() -> None:
    op.drop_table("delivery_note_lines")
    op.drop_table("delivery_notes")
    op.drop_table("delivery_files")
    op.drop_table("suppliers")
    op.drop_table("system_settings")

"""ai_chat_logs + ai_code_patches

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.String(64)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("related_exception_id", sa.Integer, sa.ForeignKey("data_exceptions.id")),
        sa.Column("user_message", sa.Text),
        sa.Column("ai_response", sa.Text),
        sa.Column("model", sa.String(64)),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("cache_read_tokens", sa.Integer),
        sa.Column("cache_creation_tokens", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("extra", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_chat_logs_session_id", "ai_chat_logs", ["session_id"])
    op.create_index("ix_ai_chat_logs_action_type", "ai_chat_logs", ["action_type"])

    op.create_table(
        "ai_code_patches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trigger_exception_id", sa.Integer, sa.ForeignKey("data_exceptions.id")),
        sa.Column("file_path", sa.String(255), nullable=False),
        sa.Column("diff_content", sa.Text, nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("approved_by", sa.String(64)),
        sa.Column("applied_at", sa.String(32)),
        sa.Column("rollback_at", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_code_patches_status", "ai_code_patches", ["status"])


def downgrade() -> None:
    op.drop_table("ai_code_patches")
    op.drop_table("ai_chat_logs")

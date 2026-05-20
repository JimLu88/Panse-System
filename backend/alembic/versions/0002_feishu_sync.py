"""feishu sync map + bindings

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feishu_sync_map",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("system_table", sa.String(64), nullable=False),
        sa.Column("system_pk", sa.String(64), nullable=False),
        sa.Column("feishu_app_token", sa.String(64), nullable=False),
        sa.Column("feishu_table_id", sa.String(64), nullable=False),
        sa.Column("feishu_record_id", sa.String(64), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("system_hash", sa.String(64)),
        sa.Column("feishu_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("system_table", "system_pk", name="uq_feishu_sync_system"),
        sa.UniqueConstraint("feishu_app_token", "feishu_table_id", "feishu_record_id", name="uq_feishu_sync_remote"),
    )
    op.create_index("ix_feishu_sync_map_system_table", "feishu_sync_map", ["system_table"])
    op.create_index("ix_feishu_sync_map_system_pk", "feishu_sync_map", ["system_pk"])

    op.create_table(
        "feishu_table_bindings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("system_table", sa.String(64), nullable=False, unique=True),
        sa.Column("feishu_app_token", sa.String(64), nullable=False),
        sa.Column("feishu_table_id", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), server_default="bidirectional"),
        sa.Column("field_mapping", sa.String(2048)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("feishu_table_bindings")
    op.drop_table("feishu_sync_map")

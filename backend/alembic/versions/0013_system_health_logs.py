"""system_health_logs

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_health_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("check_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_system_health_logs_check_name", "system_health_logs", ["check_name"])
    op.create_index("ix_system_health_logs_status", "system_health_logs", ["status"])
    op.create_index("ix_system_health_logs_check_status", "system_health_logs",
                    ["check_name", "status"])


def downgrade() -> None:
    op.drop_table("system_health_logs")

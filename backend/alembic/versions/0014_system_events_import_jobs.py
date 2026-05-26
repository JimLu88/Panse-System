"""system_events + import_jobs

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("actor", sa.String(64)),
        sa.Column("detail", sa.Text),
        sa.Column("snapshot_json", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_system_events_kind", "system_events", ["kind"])
    op.create_index("ix_system_events_kind_created", "system_events", ["kind", "created_at"])

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("sheet_name", sa.String(128), nullable=False),
        sa.Column("mapping", sa.JSON),
        sa.Column("options_json", sa.JSON),
        sa.Column("total_rows", sa.Integer, server_default="0", nullable=False),
        sa.Column("processed_rows", sa.Integer, server_default="0", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("report", sa.JSON),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_import_jobs_user_id", "import_jobs", ["user_id"])
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("import_jobs")
    op.drop_table("system_events")

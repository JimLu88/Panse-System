"""Phase 9: approval_requests + customers

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("target_table", sa.String(64)),
        sa.Column("target_id", sa.Integer),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("payload_json", sa.JSON),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("approver", sa.String(64)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("reject_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_approval_kind", "approval_requests", ["kind"])
    op.create_index("ix_approval_status", "approval_requests", ["status"])
    op.create_index("ix_approval_status_kind", "approval_requests", ["status", "kind"])

    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("phone", sa.String(32)),
        sa.Column("address", sa.String(512)),
        sa.Column("matching_key", sa.String(128), nullable=False, unique=True),
        sa.Column("tier", sa.String(16), server_default="bronze", nullable=False),
        sa.Column("first_order_at", sa.DateTime(timezone=True)),
        sa.Column("last_order_at", sa.DateTime(timezone=True)),
        sa.Column("total_orders", sa.Integer, server_default="0", nullable=False),
        sa.Column("total_revenue", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("total_returns", sa.Integer, server_default="0", nullable=False),
        sa.Column("tags", sa.JSON),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_customers_phone", "customers", ["phone"])
    op.create_index("ix_customers_matching_key", "customers", ["matching_key"])
    op.create_index("ix_customers_tier", "customers", ["tier"])


def downgrade() -> None:
    op.drop_table("customers")
    op.drop_table("approval_requests")

"""Phase 12: sales_daily_rollup

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_daily_rollup",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("product_code", sa.String(32)),
        sa.Column("sku_code", sa.String(32)),
        sa.Column("platform", sa.String(32)),
        sa.Column("order_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("qty", sa.Integer, server_default="0", nullable=False),
        sa.Column("revenue", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("net_profit", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("day", "product_code", "sku_code", "platform",
                            name="uq_rollup_dim"),
    )
    op.create_index("ix_rollup_day", "sales_daily_rollup", ["day"])
    op.create_index("ix_rollup_product_code", "sales_daily_rollup", ["product_code"])
    op.create_index("ix_rollup_day_product", "sales_daily_rollup", ["day", "product_code"])


def downgrade() -> None:
    op.drop_table("sales_daily_rollup")

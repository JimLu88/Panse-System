"""competitor prices table

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "competitor_prices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("store", sa.String(128), index=True),
        sa.Column("category", sa.String(64), index=True),
        sa.Column("product", sa.String(256)),
        sa.Column("link", sa.Text),
        sa.Column("wood", sa.String(64)),
        sa.Column("sku_name", sa.String(256), index=True),
        sa.Column("daily_price", sa.Numeric(12, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("competitor_prices")

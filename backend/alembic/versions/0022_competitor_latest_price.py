"""competitor latest price columns

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("competitor_prices") as batch:
        batch.add_column(sa.Column("latest_price", sa.Numeric(12, 2), nullable=True))
        batch.add_column(sa.Column("latest_fetched_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("fetch_status", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("competitor_prices") as batch:
        batch.drop_column("fetch_status")
        batch.drop_column("latest_fetched_at")
        batch.drop_column("latest_price")

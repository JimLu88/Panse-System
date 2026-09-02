"""Add a dedicated investigation note to factory orders.

Revision ID: 0150
Revises: 0149
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa


revision = "0150"
down_revision = "0149"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "unpaid_reason_note" not in _columns("factory_orders"):
        op.add_column("factory_orders", sa.Column("unpaid_reason_note", sa.Text()))


def downgrade() -> None:
    if "unpaid_reason_note" in _columns("factory_orders"):
        op.drop_column("factory_orders", "unpaid_reason_note")

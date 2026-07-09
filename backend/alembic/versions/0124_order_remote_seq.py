"""order.remote_seq — 远期单内部序号 (远期单不占工厂号, 改发"远期单 N")

Revision ID: 0124
Revises: 0123
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    from sqlalchemy import inspect
    return any(c["name"] == col for c in inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column("orders", "remote_seq"):
        op.add_column("orders", sa.Column("remote_seq", sa.Integer(), nullable=True))
        op.create_index("ix_orders_remote_seq", "orders", ["remote_seq"])


def downgrade() -> None:
    if _has_column("orders", "remote_seq"):
        op.drop_index("ix_orders_remote_seq", table_name="orders")
        op.drop_column("orders", "remote_seq")

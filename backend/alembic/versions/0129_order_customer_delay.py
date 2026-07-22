"""订单增加客户延期标识和延期后截止日期。

Revision ID: 0129
Revises: 0128
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    cols = _columns("orders")
    if "is_customer_delayed" not in cols:
        op.add_column(
            "orders",
            sa.Column("is_customer_delayed", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "customer_delay_deadline" not in cols:
        op.add_column("orders", sa.Column("customer_delay_deadline", sa.Date(), nullable=True))


def downgrade() -> None:
    cols = _columns("orders")
    if "customer_delay_deadline" in cols:
        op.drop_column("orders", "customer_delay_deadline")
    if "is_customer_delayed" in cols:
        op.drop_column("orders", "is_customer_delayed")

"""add alipay_flow_no to orders (支付宝流水反向匹配回填)

Revision ID: 0034
Revises: 0033
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("alipay_flow_no", sa.String(64), nullable=True))
    op.create_index("ix_orders_alipay_flow_no", "orders", ["alipay_flow_no"])


def downgrade() -> None:
    op.drop_index("ix_orders_alipay_flow_no", table_name="orders")
    op.drop_column("orders", "alipay_flow_no")

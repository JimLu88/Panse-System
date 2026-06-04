"""product_inventory: 新增成品库存高级字段 (安全库存/提前期/滞销天数/预警线)

Revision ID: 0041
Revises: 0040
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_inventory", sa.Column("safety_stock", sa.Numeric(14, 3), nullable=True))
    op.add_column("product_inventory", sa.Column("lead_time_days", sa.Integer(), nullable=True))
    op.add_column("product_inventory", sa.Column("slow_moving_days", sa.Integer(), nullable=True, server_default="60"))
    op.add_column("product_inventory", sa.Column("reorder_point", sa.Numeric(14, 3), nullable=True))


def downgrade() -> None:
    op.drop_column("product_inventory", "reorder_point")
    op.drop_column("product_inventory", "slow_moving_days")
    op.drop_column("product_inventory", "lead_time_days")
    op.drop_column("product_inventory", "safety_stock")

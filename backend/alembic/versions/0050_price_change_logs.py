"""price_change_logs 表 — 价格/成本变更留痕 (优化 #5).

Revision ID: 0050
Revises: 0049
"""
from alembic import op
import sqlalchemy as sa

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_change_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sku_code", sa.String(64), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("old_value", sa.String(64)),
        sa.Column("new_value", sa.String(64)),
        sa.Column("actor", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_price_change_logs_sku_code", "price_change_logs", ["sku_code"])


def downgrade():
    op.drop_index("ix_price_change_logs_sku_code", table_name="price_change_logs")
    op.drop_table("price_change_logs")

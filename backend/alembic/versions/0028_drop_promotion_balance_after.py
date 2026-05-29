"""推广记录表删除 操作后余额 (balance_after) 字段

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("promotion_flows") as batch_op:
        batch_op.drop_column("balance_after")


def downgrade() -> None:
    with op.batch_alter_table("promotion_flows") as batch_op:
        batch_op.add_column(sa.Column("balance_after", sa.Numeric(12, 2), nullable=True))

"""给 orders 加 warehouse 发货仓库列

Revision ID: 0038
Revises: 0037
"""
from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("warehouse", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "warehouse")

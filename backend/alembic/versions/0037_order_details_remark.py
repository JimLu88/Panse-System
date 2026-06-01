"""给 order_details 加 remark 列

Revision ID: 0037
Revises: 0036
"""
from alembic import op
import sqlalchemy as sa

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_details", sa.Column("remark", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("order_details", "remark")

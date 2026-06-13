"""orders 加 买家留言/商家备注 (平台字段, 随重导覆盖; remark 保留为人工备注)。

Revision ID: 0078
Revises: 0077
"""
import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "buyer_message" not in cols:
        op.add_column("orders", sa.Column("buyer_message", sa.Text(), nullable=True))
    if "seller_memo" not in cols:
        op.add_column("orders", sa.Column("seller_memo", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "seller_memo")
    op.drop_column("orders", "buyer_message")

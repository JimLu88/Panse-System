"""店铺/平台保证金条目 shop_deposits (多店铺手动加条目, 合计并入可用资金加项)。

幂等: 表已存在则跳过。

Revision ID: 0063
Revises: 0062
"""
import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "shop_deposits" in insp.get_table_names():
        return
    op.create_table(
        "shop_deposits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(64), nullable=True),
        sa.Column("shop_name", sa.String(128), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("remark", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("shop_deposits")

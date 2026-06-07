"""结算账单明细表 order_settlements (微信/聚合 billDetail 逐笔对账)。

幂等: 表已存在则跳过。

Revision ID: 0058
Revises: 0057
"""
import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "order_settlements" in insp.get_table_names():
        return
    op.create_table(
        "order_settlements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="wechat"),
        sa.Column("pay_no", sa.String(64), nullable=False),
        sa.Column("order_no", sa.String(64), nullable=True),
        sa.Column("settle_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_type", sa.String(32), nullable=True),
        sa.Column("income", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("expense", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("remark", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_order_settlements_pay_no", "order_settlements", ["pay_no"], unique=True)
    op.create_index("ix_order_settlements_order_no", "order_settlements", ["order_no"])


def downgrade() -> None:
    op.drop_table("order_settlements")

"""工厂逐单对账明细 factory_recon_items (工厂侧对账单 xlsx 每行=一笔工厂结算)。

幂等: 表已存在则跳过。

Revision ID: 0062
Revises: 0061
"""
import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "factory_recon_items" in insp.get_table_names():
        return
    op.create_table(
        "factory_recon_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_sheet", sa.String(64), nullable=True),
        sa.Column("doc_no", sa.String(32), nullable=True),
        sa.Column("order_no", sa.String(64), nullable=True),
        sa.Column("extra_order_no1", sa.String(64), nullable=True),
        sa.Column("extra_order_no2", sa.String(64), nullable=True),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("qty", sa.Integer, nullable=False, server_default="1"),
        sa.Column("settle_price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("customer_info", sa.String(128), nullable=True),
        sa.Column("order_date", sa.Date, nullable=True),
        sa.Column("ship_date", sa.Date, nullable=True),
        sa.Column("remark", sa.Text, nullable=True),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("settle_reason", sa.Text, nullable=True),
        sa.Column("resolved_by", sa.String(64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="import"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_factory_recon_items_order_no", "factory_recon_items", ["order_no"])
    op.create_index("ix_factory_recon_items_order_date", "factory_recon_items", ["order_date"])


def downgrade() -> None:
    op.drop_table("factory_recon_items")

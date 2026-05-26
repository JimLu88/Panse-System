"""daily_operations extra fields + order_details table

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    # daily_operations — add columns from 日常经营 Excel
    op.add_column("daily_operations", sa.Column("payment_account", sa.String(64), nullable=True))
    op.add_column("daily_operations", sa.Column("expense_type", sa.String(64), nullable=True))
    op.add_column("daily_operations", sa.Column("recipient", sa.String(128), nullable=True))
    op.add_column("daily_operations", sa.Column("payment_method", sa.String(64), nullable=True))
    op.add_column("daily_operations", sa.Column("alipay_flow_no", sa.String(64), nullable=True))
    op.add_column("daily_operations", sa.Column("invoice_status", sa.String(32), nullable=True))

    # order_details — new table for 订单细节 (feishu tblYLdjivHwpu5ea)
    op.create_table(
        "order_details",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sync_key", sa.String(128), unique=True, nullable=True),
        sa.Column("order_no", sa.String(64), nullable=True),
        sa.Column("factory_order_no", sa.String(64), nullable=True),
        sa.Column("product_code", sa.String(64), nullable=True),
        sa.Column("product_name", sa.String(255), nullable=True),
        sa.Column("sku_code", sa.String(64), nullable=True),
        sa.Column("sku_name", sa.String(255), nullable=True),
        sa.Column("bom_material_code", sa.String(64), nullable=True),
        sa.Column("material_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_order_details_order_no", "order_details", ["order_no"])
    op.create_index("ix_order_details_factory_order_no", "order_details", ["factory_order_no"])
    op.create_index("ix_order_details_product_code", "order_details", ["product_code"])


def downgrade():
    op.drop_index("ix_order_details_product_code", "order_details")
    op.drop_index("ix_order_details_factory_order_no", "order_details")
    op.drop_index("ix_order_details_order_no", "order_details")
    op.drop_table("order_details")

    op.drop_column("daily_operations", "invoice_status")
    op.drop_column("daily_operations", "alipay_flow_no")
    op.drop_column("daily_operations", "payment_method")
    op.drop_column("daily_operations", "recipient")
    op.drop_column("daily_operations", "expense_type")
    op.drop_column("daily_operations", "payment_account")

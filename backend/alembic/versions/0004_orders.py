"""orders + factory_orders + part_purchases

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("order_no", sa.String(64), nullable=False, unique=True),
        sa.Column("is_refill", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("order_date", sa.Date),
        sa.Column("ship_date", sa.Date),
        sa.Column("customer_name", sa.String(64)),
        sa.Column("customer_phone", sa.String(32)),
        sa.Column("customer_address", sa.String(255)),
        sa.Column("product_code", sa.String(32)),
        sa.Column("product_name", sa.String(255)),
        sa.Column("sku", sa.String(255)),
        sa.Column("sku_code", sa.String(32)),
        sa.Column("is_custom", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("qty", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_payment"),
        sa.Column("carrier", sa.String(64)),
        sa.Column("tracking_no", sa.String(128)),
        sa.Column("install_ticket_no", sa.String(64)),
        sa.Column("theoretical_cost", sa.Numeric(12, 2)),
        sa.Column("actual_cost", sa.Numeric(12, 2)),
        sa.Column("actual_freight", sa.Numeric(12, 2)),
        sa.Column("upstairs_fee", sa.Numeric(12, 2)),
        sa.Column("install_fee", sa.Numeric(12, 2)),
        sa.Column("compensation_fee", sa.Numeric(12, 2)),
        sa.Column("paid_amount", sa.Numeric(12, 2)),
        sa.Column("remark", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_orders_platform", "orders", ["platform"])
    op.create_index("ix_orders_order_no", "orders", ["order_no"])
    op.create_index("ix_orders_order_date", "orders", ["order_date"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_product_code", "orders", ["product_code"])
    op.create_index("ix_orders_sku_code", "orders", ["sku_code"])
    op.create_index("ix_orders_platform_date", "orders", ["platform", "order_date"])

    op.create_table(
        "factory_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("factory_order_no", sa.String(64), nullable=False, unique=True),
        sa.Column("platform_order_no", sa.String(64)),
        sa.Column("factory_name", sa.String(128)),
        sa.Column("order_date", sa.Date),
        sa.Column("expected_delivery", sa.Date),
        sa.Column("actual_delivery", sa.Date),
        sa.Column("product_code", sa.String(32)),
        sa.Column("sku", sa.String(255)),
        sa.Column("qty", sa.Integer, nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2)),
        sa.Column("factory_bill_amount", sa.Numeric(12, 2)),
        sa.Column("expected_amount", sa.Numeric(12, 2)),
        sa.Column("payment_method", sa.String(32)),
        sa.Column("payment_status", sa.String(32), nullable=False, server_default="unpaid"),
        sa.Column("payment_date", sa.Date),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("carrier", sa.String(64)),
        sa.Column("tracking_no", sa.String(128)),
        sa.Column("remark", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_factory_orders_no", "factory_orders", ["factory_order_no"])
    op.create_index("ix_factory_orders_platform", "factory_orders", ["platform_order_no"])
    op.create_index("ix_factory_orders_product", "factory_orders", ["product_code"])

    op.create_table(
        "part_purchases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("purchase_no", sa.String(32), nullable=False, unique=True),
        sa.Column("supplier", sa.String(128)),
        sa.Column("purchase_date", sa.Date),
        sa.Column("material_code", sa.String(32)),
        sa.Column("material_name", sa.String(255)),
        sa.Column("spec", sa.String(255)),
        sa.Column("qty", sa.Numeric(12, 4), server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2)),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("tracking_no", sa.String(128)),
        sa.Column("freight", sa.Numeric(12, 2)),
        sa.Column("total_amount", sa.Numeric(12, 2)),
        sa.Column("purchase_type", sa.String(32)),
        sa.Column("related_order_no", sa.String(64)),
        sa.Column("payment_method", sa.String(32)),
        sa.Column("payment_status", sa.String(32), nullable=False, server_default="unpaid"),
        sa.Column("payment_date", sa.Date),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("remark", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_part_purchases_no", "part_purchases", ["purchase_no"])
    op.create_index("ix_part_purchases_date", "part_purchases", ["purchase_date"])
    op.create_index("ix_part_purchases_material", "part_purchases", ["material_code"])


def downgrade() -> None:
    op.drop_table("part_purchases")
    op.drop_table("factory_orders")
    op.drop_table("orders")

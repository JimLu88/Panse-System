"""Classify same-order top-ups that have no separate factory cost.

Revision ID: 0151
Revises: 0150
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa


revision = "0151"
down_revision = "0150"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    columns = _columns("factory_orders")
    if "factory_cost_type" not in columns:
        op.add_column(
            "factory_orders",
            sa.Column("factory_cost_type", sa.String(length=32), nullable=False, server_default="normal"),
        )
    if "related_primary_order_no" not in columns:
        op.add_column("factory_orders", sa.Column("related_primary_order_no", sa.String(length=64)))
    if "ix_factory_orders_related_primary_order_no" not in _indexes("factory_orders"):
        op.create_index(
            "ix_factory_orders_related_primary_order_no",
            "factory_orders",
            ["related_primary_order_no"],
            unique=False,
        )
    # 已导入的工厂逐单账单中，extra_order_no/订单2 是最可靠的历史关联证据。
    # 仅在订单1和订单2都能落到有效工厂单时回填，绝不按金额大小猜测。
    if "factory_recon_items" in sa.inspect(op.get_bind()).get_table_names():
        op.execute(sa.text("""
            UPDATE factory_orders AS topup
               SET factory_cost_type = 'same_order_topup',
                   related_primary_order_no = recon.order_no,
                   factory_bill_amount = 0
              FROM factory_recon_items AS recon
             WHERE topup.voided_at IS NULL
               AND recon.order_no IS NOT NULL
               AND recon.order_no <> topup.platform_order_no
               AND topup.platform_order_no IS NOT NULL
               AND topup.platform_order_no IN (recon.extra_order_no1, recon.extra_order_no2)
               AND EXISTS (
                   SELECT 1
                     FROM factory_orders AS primary_order
                    WHERE primary_order.voided_at IS NULL
                      AND primary_order.platform_order_no = recon.order_no
               )
        """))


def downgrade() -> None:
    columns = _columns("factory_orders")
    if "related_primary_order_no" in columns:
        if "ix_factory_orders_related_primary_order_no" in _indexes("factory_orders"):
            op.drop_index("ix_factory_orders_related_primary_order_no", table_name="factory_orders")
        op.drop_column("factory_orders", "related_primary_order_no")
    if "factory_cost_type" in columns:
        op.drop_column("factory_orders", "factory_cost_type")

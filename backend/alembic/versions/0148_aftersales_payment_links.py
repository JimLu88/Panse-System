"""Add auditable personal-Alipay after-sales payment links.

Revision ID: 0148
Revises: 0147
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa


revision = "0148"
down_revision = "0147"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "after_sales",
        sa.Column("payment_link_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "after_sales_payment_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alipay_flow_id", sa.Integer(), sa.ForeignKey("alipay_flows.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("allocation_key", sa.String(64), nullable=False, server_default="full"),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("after_sales_id", sa.Integer(), sa.ForeignKey("after_sales.id", ondelete="SET NULL")),
        sa.Column("wanshifu_order_id", sa.Integer(), sa.ForeignKey("wanshifu_orders.id", ondelete="SET NULL")),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("match_method", sa.String(40)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("extracted_order_no", sa.String(64)),
        sa.Column("extracted_customer_name", sa.String(64)),
        sa.Column("evidence_json", sa.JSON()),
        sa.Column("decision_note", sa.Text()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("decided_by", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("alipay_flow_id", "allocation_key", name="uq_after_sales_payment_flow_allocation"),
        sa.CheckConstraint("allocated_amount > 0", name="ck_after_sales_payment_amount_positive"),
        sa.CheckConstraint(
            "status in ('proposed','confirmed','rejected','voided')",
            name="ck_after_sales_payment_status",
        ),
        sa.CheckConstraint(
            "category in ('price_difference','review_refund','customer_compensation',"
            "'repair_service','onsite_service','return_service','misc_after_sales')",
            name="ck_after_sales_payment_category",
        ),
        sa.CheckConstraint("version > 0", name="ck_after_sales_payment_version_positive"),
    )
    op.create_index("ix_after_sales_payment_links_alipay_flow_id", "after_sales_payment_links", ["alipay_flow_id"])
    op.create_index("ix_after_sales_payment_links_order_id", "after_sales_payment_links", ["order_id"])
    op.create_index("ix_after_sales_payment_links_after_sales_id", "after_sales_payment_links", ["after_sales_id"])
    op.create_index("ix_after_sales_payment_links_wanshifu_order_id", "after_sales_payment_links", ["wanshifu_order_id"])
    op.create_index("ix_after_sales_payment_links_status", "after_sales_payment_links", ["status"])
    op.create_index("ix_after_sales_payment_status_category", "after_sales_payment_links", ["status", "category"])


def downgrade() -> None:
    op.drop_table("after_sales_payment_links")
    op.drop_column("after_sales", "payment_link_managed")

"""打包费支付流水按费用账期分配。

Revision ID: 0128
Revises: 0127
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa


revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("packing_payment_allocations"):
        return
    op.create_table(
        "packing_payment_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alipay_flow_id", sa.Integer(), nullable=False),
        sa.Column("bill_month", sa.String(7), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("allocation_source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["alipay_flow_id"], ["alipay_flows.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("alipay_flow_id", "bill_month", name="uq_packing_payment_flow_month"),
    )
    op.create_index("ix_packing_payment_allocations_alipay_flow_id", "packing_payment_allocations", ["alipay_flow_id"])
    op.create_index("ix_packing_payment_allocations_bill_month", "packing_payment_allocations", ["bill_month"])
    op.create_index("ix_packing_payment_month_flow", "packing_payment_allocations", ["bill_month", "alipay_flow_id"])


def downgrade() -> None:
    if _has_table("packing_payment_allocations"):
        op.drop_table("packing_payment_allocations")

"""Distinguish after-sales costs from normal order installation costs.

Revision ID: 0149
Revises: 0148
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "0149"
down_revision = "0148"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "after_sales_payment_links",
        sa.Column(
            "accounting_target", sa.String(32), nullable=False,
            server_default="aftersales",
        ),
    )
    op.create_check_constraint(
        "ck_after_sales_payment_accounting_target",
        "after_sales_payment_links",
        "accounting_target in ('aftersales','order_install')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_after_sales_payment_accounting_target",
        "after_sales_payment_links",
        type_="check",
    )
    op.drop_column("after_sales_payment_links", "accounting_target")

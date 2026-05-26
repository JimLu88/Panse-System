"""finance tables: alipay_flows, account_balances, refill_records, factory_reconciliations

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alipay_flows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account", sa.String(32), nullable=False),
        sa.Column("transaction_no", sa.String(64), nullable=False),
        sa.Column("transaction_time", sa.DateTime(timezone=True)),
        sa.Column("transaction_type", sa.String(64)),
        sa.Column("counterparty", sa.String(255)),
        sa.Column("counterparty_account", sa.String(255)),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("related_order_no", sa.String(64)),
        sa.Column("balance", sa.Numeric(14, 2)),
        sa.Column("reconciliation_status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("reconciliation_type", sa.String(32)),
        sa.Column("remark", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account", "transaction_no", name="uq_alipay_flow_acct_no"),
    )
    op.create_index("ix_alipay_flows_account", "alipay_flows", ["account"])
    op.create_index("ix_alipay_flows_transaction_no", "alipay_flows", ["transaction_no"])
    op.create_index("ix_alipay_flows_transaction_time", "alipay_flows", ["transaction_time"])
    op.create_index("ix_alipay_flows_related_order_no", "alipay_flows", ["related_order_no"])
    op.create_index("ix_alipay_flows_recon_status", "alipay_flows", ["reconciliation_status"])
    op.create_index("ix_alipay_flows_acct_time", "alipay_flows", ["account", "transaction_time"])

    op.create_table(
        "account_balances",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_name", sa.String(64), nullable=False),
        sa.Column("account_no", sa.String(128)),
        sa.Column("period_year", sa.Integer, nullable=False),
        sa.Column("period_month", sa.Integer, nullable=False),
        sa.Column("opening_balance", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("income", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("expense", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("closing_balance", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("remark", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_name", "period_year", "period_month", name="uq_account_balance_period"),
    )

    op.create_table(
        "refill_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_no", sa.String(64), nullable=False),
        sa.Column("buyer_nick", sa.String(128)),
        sa.Column("refill_date", sa.Date),
        sa.Column("product_code", sa.String(32)),
        sa.Column("product_name", sa.String(255)),
        sa.Column("sku", sa.String(255)),
        sa.Column("order_amount", sa.Numeric(12, 2)),
        sa.Column("qty", sa.Integer, nullable=False, server_default="1"),
        sa.Column("refill_cost", sa.Numeric(12, 2)),
        sa.Column("refill_freight", sa.Numeric(12, 2)),
        sa.Column("platform_fee", sa.Numeric(12, 2)),
        sa.Column("total_cost", sa.Numeric(12, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_refill_records_order_no", "refill_records", ["order_no"])
    op.create_index("ix_refill_records_date", "refill_records", ["refill_date"])

    op.create_table(
        "factory_reconciliations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("period_start", sa.Date),
        sa.Column("period_end", sa.Date),
        sa.Column("factory_name", sa.String(128), nullable=False),
        sa.Column("order_amount", sa.Numeric(14, 2)),
        sa.Column("bill_amount", sa.Numeric(14, 2)),
        sa.Column("paid_amount", sa.Numeric(14, 2)),
        sa.Column("reconciled_at", sa.Date),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("diff_amount", sa.Numeric(14, 2), server_default="0"),
        sa.Column("diff_reason", sa.Text),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("remark", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_factory_recon_factory", "factory_reconciliations", ["factory_name"])


def downgrade() -> None:
    op.drop_table("factory_reconciliations")
    op.drop_table("refill_records")
    op.drop_table("account_balances")
    op.drop_table("alipay_flows")

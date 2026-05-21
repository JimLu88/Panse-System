"""marketing + after-sales tables: samples, brand_marketing, promotion_flows,
outsourcing_expenses, after_sales, wood_losses

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "samples",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sample_no", sa.String(32), nullable=False, unique=True),
        sa.Column("product_code", sa.String(32)),
        sa.Column("product_name", sa.String(255)),
        sa.Column("sku", sa.String(255)),
        sa.Column("sample_type", sa.String(32)),
        sa.Column("qty", sa.Integer, nullable=False, server_default="1"),
        sa.Column("made_at", sa.Date),
        sa.Column("cost", sa.Numeric(12, 2)),
        sa.Column("location", sa.String(128)),
        sa.Column("status", sa.String(32)),
        sa.Column("usage", sa.String(128)),
        sa.Column("remark", sa.Text),
        *_ts(),
    )
    op.create_index("ix_samples_product_code", "samples", ["product_code"])
    op.create_index("ix_samples_sample_no", "samples", ["sample_no"])

    op.create_table(
        "brand_marketing",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_name", sa.String(255), nullable=False),
        sa.Column("project_type", sa.String(64)),
        sa.Column("partner", sa.String(255)),
        sa.Column("start_date", sa.Date),
        sa.Column("end_date", sa.Date),
        sa.Column("budget", sa.Numeric(12, 2)),
        sa.Column("actual_spend", sa.Numeric(12, 2)),
        sa.Column("payment_date", sa.Date),
        sa.Column("status", sa.String(32)),
        sa.Column("effect_eval", sa.Text),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("remark", sa.Text),
        *_ts(),
    )

    op.create_table(
        "promotion_flows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("transaction_date", sa.Date),
        sa.Column("flow_type", sa.String(32)),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2)),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("remark", sa.Text),
        *_ts(),
    )
    op.create_index("ix_promotion_flows_date", "promotion_flows", ["transaction_date"])

    op.create_table(
        "outsourcing_expenses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("payee", sa.String(128), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("project", sa.String(128)),
        sa.Column("related_order_no", sa.String(64)),
        sa.Column("cost_category", sa.String(32)),
        sa.Column("payment_date", sa.Date),
        sa.Column("remark", sa.Text),
        *_ts(),
    )
    op.create_index("ix_outsourcing_payment_date", "outsourcing_expenses", ["payment_date"])

    op.create_table(
        "after_sales",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform_order_no", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(255)),
        sa.Column("compensation_fee", sa.Numeric(12, 2)),
        sa.Column("good_review_refund", sa.Numeric(12, 2)),
        sa.Column("in_platform_total", sa.Numeric(12, 2)),
        sa.Column("direct_compensation", sa.Numeric(12, 2)),
        sa.Column("second_visit_fee", sa.Numeric(12, 2)),
        sa.Column("return_pack_freight", sa.Numeric(12, 2)),
        sa.Column("out_platform_total", sa.Numeric(12, 2)),
        sa.Column("refill_sku", sa.String(255)),
        sa.Column("refill_tracking_no", sa.String(128)),
        sa.Column("refill_freight", sa.Numeric(12, 2)),
        sa.Column("wanshifu_deduction", sa.Numeric(12, 2)),
        sa.Column("factory_compensation", sa.Numeric(12, 2)),
        sa.Column("logistics_compensation", sa.Numeric(12, 2)),
        sa.Column("alipay_flow_no", sa.String(64)),
        sa.Column("second_inbound_confirmed", sa.String(8)),
        sa.Column("processed_at", sa.Date),
        sa.Column("status", sa.String(32)),
        sa.Column("customer_satisfaction", sa.String(32)),
        sa.Column("remark", sa.Text),
        *_ts(),
    )
    op.create_index("ix_after_sales_order_no", "after_sales", ["platform_order_no"])

    op.create_table(
        "wood_losses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("purchase_date", sa.Date),
        sa.Column("wood_type", sa.String(64)),
        sa.Column("spec", sa.String(128)),
        sa.Column("unit", sa.String(16)),
        sa.Column("inbound_qty", sa.Numeric(12, 4)),
        sa.Column("used_qty", sa.Numeric(12, 4)),
        sa.Column("loss_qty", sa.Numeric(12, 4)),
        sa.Column("loss_rate_pct", sa.Numeric(8, 4)),
        sa.Column("related_product_qty", sa.Numeric(12, 4)),
        sa.Column("reason", sa.String(255)),
        sa.Column("disposition", sa.String(255)),
        sa.Column("remark", sa.Text),
        *_ts(),
    )


def downgrade() -> None:
    for t in ["wood_losses", "after_sales", "outsourcing_expenses",
              "promotion_flows", "brand_marketing", "samples"]:
        op.drop_table(t)

"""万师傅安装账单 + 物流费账单 (安装费 / 物流费对账数据源)

Revision ID: 0035
Revises: 0034
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wanshifu_bills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bill_date", sa.Date(), nullable=True),
        sa.Column("order_no", sa.String(64), nullable=True),
        sa.Column("service_type", sa.String(64), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("import_job_id", sa.Integer(), sa.ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_wanshifu_bills_bill_date", "wanshifu_bills", ["bill_date"])
    op.create_index("ix_wanshifu_bills_order_no", "wanshifu_bills", ["order_no"])
    op.create_index("ix_wanshifu_bills_import_job_id", "wanshifu_bills", ["import_job_id"])

    op.create_table(
        "logistics_bills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bill_date", sa.Date(), nullable=True),
        sa.Column("carrier", sa.String(64), nullable=True),
        sa.Column("tracking_no", sa.String(128), nullable=True),
        sa.Column("order_no", sa.String(64), nullable=True),
        sa.Column("weight_kg", sa.Numeric(8, 3), nullable=True),
        sa.Column("freight_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("import_job_id", sa.Integer(), sa.ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_logistics_bills_bill_date", "logistics_bills", ["bill_date"])
    op.create_index("ix_logistics_bills_carrier", "logistics_bills", ["carrier"])
    op.create_index("ix_logistics_bills_tracking_no", "logistics_bills", ["tracking_no"])
    op.create_index("ix_logistics_bills_order_no", "logistics_bills", ["order_no"])
    op.create_index("ix_logistics_bills_import_job_id", "logistics_bills", ["import_job_id"])


def downgrade() -> None:
    op.drop_table("logistics_bills")
    op.drop_table("wanshifu_bills")

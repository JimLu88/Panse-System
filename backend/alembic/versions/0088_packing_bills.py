"""packing_bills 新表 — 打包费手写账单 (手写本拍照 OCR 逐行入库, 用户 2026-06-21)。

用途: Σ packing_fee (excluded=False) = 当月应付打包费; 「改客户/不计入」行 excluded=True
自动剔除不计入。手写姓名 OCR 准确率有限 → 走预览人工复核再 commit; 配单按 order_no/客户名唯一。

Revision ID: 0088
Revises: 0087
"""
import sqlalchemy as sa
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "packing_bills" in insp.get_table_names():
        return
    op.create_table(
        "packing_bills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bill_month", sa.String(length=7), nullable=True),       # 账期 YYYY-MM
        sa.Column("row_date", sa.Date(), nullable=True),
        sa.Column("customer_name", sa.String(length=64), nullable=True),   # 手写客户/收货人名
        sa.Column("order_no", sa.String(length=64), nullable=True),        # OCR 偶尔读到的单号
        sa.Column("matched_order_no", sa.String(length=64), nullable=True),
        sa.Column("match_method", sa.String(length=32), nullable=True),    # order_no/name_unique/multi/none/manual
        sa.Column("match_note", sa.Text(), nullable=True),
        sa.Column("product", sa.String(length=128), nullable=True),
        sa.Column("packing_fee", sa.Numeric(12, 2), nullable=True),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exclude_reason", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),          # 手写识别把握 0-1
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_image", sa.String(length=255), nullable=True),   # 原图归档引用
        sa.Column("import_job_id", sa.Integer(),
                  sa.ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_packing_bills_bill_month", "packing_bills", ["bill_month"])
    op.create_index("ix_packing_bills_customer_name", "packing_bills", ["customer_name"])
    op.create_index("ix_packing_bills_matched_order_no", "packing_bills", ["matched_order_no"])


def downgrade() -> None:
    try:
        op.drop_table("packing_bills")
    except Exception:  # noqa: BLE001
        pass

"""samples 加 关联订单号/售出日期 (样品售出: 杭州→江西修复→发客户; 修复费/转运费走配件采购)。

Revision ID: 0080
Revises: 0079
"""
import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("samples")}
    if "related_order_no" not in cols:
        op.add_column("samples", sa.Column("related_order_no", sa.String(64), nullable=True))
        op.create_index("ix_samples_related_order_no", "samples", ["related_order_no"])
    if "sold_at" not in cols:
        op.add_column("samples", sa.Column("sold_at", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("samples", "sold_at")
    try:
        op.drop_index("ix_samples_related_order_no", table_name="samples")
    except Exception:
        pass
    op.drop_column("samples", "related_order_no")

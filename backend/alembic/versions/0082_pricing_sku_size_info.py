"""定价表 pricing_sku 加 按SKU成品尺寸 size_info (从 SKU 尺寸图读出回填; 下单图按订单 sku_code 取此)。

Revision ID: 0082
Revises: 0081
"""
import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("pricing_sku")}
    if "size_info" not in cols:
        op.add_column("pricing_sku", sa.Column("size_info", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("pricing_sku", "size_info")

"""定价表加 淘宝宝贝标题 taobao_title (订单无编码时按长标题匹配回填 product_code)。

Revision ID: 0081
Revises: 0080
"""
import sqlalchemy as sa
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("pricing_sku")}
    if "taobao_title" not in cols:
        op.add_column("pricing_sku", sa.Column("taobao_title", sa.String(255), nullable=True))
        op.create_index("ix_pricing_sku_taobao_title", "pricing_sku", ["taobao_title"])


def downgrade() -> None:
    try:
        op.drop_index("ix_pricing_sku_taobao_title", table_name="pricing_sku")
    except Exception:
        pass
    op.drop_column("pricing_sku", "taobao_title")

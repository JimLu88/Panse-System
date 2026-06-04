"""pricing_sku 补全缺失列 — 产品名称/备注 + 淘宝链接 + 中促/大促平台立减、88VIP佣金.

Revision ID: 0045
Revises: 0044
"""
from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    # pricing_sku: 产品名称 + 备注
    op.add_column("pricing_sku", sa.Column("product_name", sa.String(255), nullable=True))
    op.add_column("pricing_sku", sa.Column("remark", sa.Text, nullable=True))

    # pricing_sku_promo: 淘宝链接 + 中促/大促平台立减 + 88VIP佣金
    op.add_column("pricing_sku_promo", sa.Column("taobao_url", sa.String(512), nullable=True))
    op.add_column("pricing_sku_promo", sa.Column("mid_platform_discount", sa.Numeric(10, 6), nullable=True))
    op.add_column("pricing_sku_promo", sa.Column("mid_vip_commission", sa.Numeric(12, 2), nullable=True))
    op.add_column("pricing_sku_promo", sa.Column("big_platform_discount", sa.Numeric(10, 6), nullable=True))
    op.add_column("pricing_sku_promo", sa.Column("big_vip_commission", sa.Numeric(12, 2), nullable=True))


def downgrade():
    op.drop_column("pricing_sku_promo", "big_vip_commission")
    op.drop_column("pricing_sku_promo", "big_platform_discount")
    op.drop_column("pricing_sku_promo", "mid_vip_commission")
    op.drop_column("pricing_sku_promo", "mid_platform_discount")
    op.drop_column("pricing_sku_promo", "taobao_url")
    op.drop_column("pricing_sku", "remark")
    op.drop_column("pricing_sku", "product_name")

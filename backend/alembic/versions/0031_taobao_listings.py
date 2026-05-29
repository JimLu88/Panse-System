"""淘宝商品导出对应表 taobao_listings (Task 5)

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "taobao_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("taobao_item_id", sa.String(length=32), nullable=False),
        sa.Column("taobao_sku_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("merchant_code", sa.String(length=64), nullable=True),
        sa.Column("sku_spec", sa.String(length=255), nullable=True),
        sa.Column("category_name", sa.String(length=255), nullable=True),
        sa.Column("list_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("sku_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("sku_code", sa.String(length=32), nullable=True),
        sa.Column("product_code", sa.String(length=32), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("taobao_item_id", "taobao_sku_id", name="uq_taobao_item_sku"),
    )
    op.create_index("ix_taobao_listings_taobao_item_id", "taobao_listings", ["taobao_item_id"])
    op.create_index("ix_taobao_listings_taobao_sku_id", "taobao_listings", ["taobao_sku_id"])
    op.create_index("ix_taobao_listings_merchant_code", "taobao_listings", ["merchant_code"])
    op.create_index("ix_taobao_listings_sku_code", "taobao_listings", ["sku_code"])
    op.create_index("ix_taobao_listings_product_code", "taobao_listings", ["product_code"])
    op.create_index("ix_taobao_listings_merchant_match", "taobao_listings", ["merchant_code", "matched"])


def downgrade() -> None:
    op.drop_index("ix_taobao_listings_merchant_match", table_name="taobao_listings")
    op.drop_index("ix_taobao_listings_product_code", table_name="taobao_listings")
    op.drop_index("ix_taobao_listings_sku_code", table_name="taobao_listings")
    op.drop_index("ix_taobao_listings_merchant_code", table_name="taobao_listings")
    op.drop_index("ix_taobao_listings_taobao_sku_id", table_name="taobao_listings")
    op.drop_index("ix_taobao_listings_taobao_item_id", table_name="taobao_listings")
    op.drop_table("taobao_listings")

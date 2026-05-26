"""pricing_sku table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pricing_sku",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_code", sa.String(32), nullable=False),
        sa.Column("sku", sa.String(255)),
        sa.Column("sku_code", sa.String(32), nullable=False, unique=True),
        sa.Column("size_category", sa.String(16)),
        sa.Column("list_price", sa.Numeric(12, 2)),
        sa.Column("daily_price", sa.Numeric(12, 2)),
        sa.Column("small_promo", sa.Numeric(12, 2)),
        sa.Column("mid_promo", sa.Numeric(12, 2)),
        sa.Column("big_promo", sa.Numeric(12, 2)),
        sa.Column("big_promo_margin", sa.Numeric(12, 2)),
        sa.Column("gross_margin_rate", sa.Numeric(10, 6)),
        sa.Column("accounting_cost", sa.Numeric(12, 2)),
        sa.Column("platform_fee_rate", sa.Numeric(10, 6)),
        sa.Column("tax", sa.Numeric(12, 2)),
        sa.Column("physical_cost", sa.Numeric(12, 2)),
        sa.Column("logistics_cost", sa.Numeric(12, 2)),
        sa.Column("install_cost", sa.Numeric(12, 2)),
        sa.Column("factory_cost", sa.Numeric(12, 2)),
        sa.Column("wood_cost", sa.Numeric(12, 2)),
        sa.Column("packaging_cost", sa.Numeric(12, 2)),
        sa.Column("external_parts_cost", sa.Numeric(12, 2)),
        sa.Column("image_url", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pricing_sku_product_code", "pricing_sku", ["product_code"])
    op.create_index("ix_pricing_sku_sku_code", "pricing_sku", ["sku_code"])
    op.create_index("ix_pricing_sku_size", "pricing_sku", ["size_category"])


def downgrade() -> None:
    op.drop_table("pricing_sku")

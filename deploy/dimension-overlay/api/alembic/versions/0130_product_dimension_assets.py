"""产品细节尺寸矢量资产与 ERP 联动。

Revision ID: 0130
Revises: 0129
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("product_dimension_assets"):
        return
    op.create_table(
        "product_dimension_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_code", sa.String(32), sa.ForeignKey("products.code", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_psd", sa.String(255), nullable=True),
        sa.Column("svg_relpath", sa.String(512), nullable=False),
        sa.Column("preview_relpath", sa.String(512), nullable=True),
        sa.Column("metadata_relpath", sa.String(512), nullable=True),
        sa.Column("dimension_data", sa.JSON(), nullable=False),
        sa.Column("erp_dimensions", sa.JSON(), nullable=False),
        sa.Column("sku_variants", sa.JSON(), nullable=False),
        sa.Column("mapping_status", sa.String(32), nullable=False, server_default="confirmed"),
        sa.Column("match_confidence", sa.String(32), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("product_code", "asset_key", name="uq_product_dimension_product_asset"),
    )
    op.create_index("ix_product_dimension_assets_product_code", "product_dimension_assets", ["product_code"])


def downgrade() -> None:
    if _has_table("product_dimension_assets"):
        op.drop_table("product_dimension_assets")

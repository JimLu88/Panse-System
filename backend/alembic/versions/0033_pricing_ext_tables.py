"""pricing_ext_tables: 配件成本拆分 + 活动价格表

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pricing_sku_costs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_code", sa.String(32), nullable=False, unique=True, index=True),
        # 22 配件成本字段
        sa.Column("rock_slab", sa.Numeric(12, 2), nullable=True),
        sa.Column("drawer_rail", sa.Numeric(12, 2), nullable=True),
        sa.Column("led_strip", sa.Numeric(12, 2), nullable=True),
        sa.Column("glass", sa.Numeric(12, 2), nullable=True),
        sa.Column("electric_rail", sa.Numeric(12, 2), nullable=True),
        sa.Column("packing_sheet", sa.Numeric(12, 2), nullable=True),
        sa.Column("iron_pin", sa.Numeric(12, 2), nullable=True),
        sa.Column("connector", sa.Numeric(12, 2), nullable=True),
        sa.Column("aluminum_rail", sa.Numeric(12, 2), nullable=True),
        sa.Column("plastic_rail", sa.Numeric(12, 2), nullable=True),
        sa.Column("mini_handle", sa.Numeric(12, 2), nullable=True),
        sa.Column("nail_free_glue", sa.Numeric(12, 2), nullable=True),
        sa.Column("engraving", sa.Numeric(12, 2), nullable=True),
        sa.Column("acrylic_strip", sa.Numeric(12, 2), nullable=True),
        sa.Column("embedded_sleeve", sa.Numeric(12, 2), nullable=True),
        sa.Column("cable_mgmt", sa.Numeric(12, 2), nullable=True),
        sa.Column("back_panel", sa.Numeric(12, 2), nullable=True),
        sa.Column("stainless_trim", sa.Numeric(12, 2), nullable=True),
        sa.Column("leg", sa.Numeric(12, 2), nullable=True),
        sa.Column("soft_pack", sa.Numeric(12, 2), nullable=True),
        sa.Column("bed_board", sa.Numeric(12, 2), nullable=True),
        sa.Column("other_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("other_desc", sa.Text(), nullable=True),
        sa.Column("parts_remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    op.create_table(
        "pricing_sku_promo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_code", sa.String(32), nullable=False, unique=True, index=True),
        # 淘宝
        sa.Column("taobao_item_id", sa.String(64), nullable=True),
        sa.Column("taobao_sku_id", sa.String(64), nullable=True),
        sa.Column("taobao_activity_price", sa.Numeric(12, 2), nullable=True),
        # 店内活动
        sa.Column("shop_promo_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("shop_internal_promo", sa.Numeric(12, 2), nullable=True),
        sa.Column("shop_internal_final", sa.Numeric(12, 2), nullable=True),
        # 无国补中促
        sa.Column("mid_shop_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("mid_buyer_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("mid_shop_receipt", sa.Numeric(12, 2), nullable=True),
        sa.Column("mid_vip_final", sa.Numeric(12, 2), nullable=True),
        # 无国补大促
        sa.Column("big_shop_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("big_buyer_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("big_shop_receipt", sa.Numeric(12, 2), nullable=True),
        sa.Column("big_vip_final", sa.Numeric(12, 2), nullable=True),
        # 小红书
        sa.Column("xhs_item_id", sa.String(64), nullable=True),
        sa.Column("xhs_sku_name", sa.String(255), nullable=True),
        sa.Column("xhs_sku_id", sa.String(64), nullable=True),
        sa.Column("xhs_list_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("xhs_activity_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("xhs_promo_discount", sa.Numeric(10, 6), nullable=True),
        sa.Column("xhs_promo_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pricing_sku_promo")
    op.drop_table("pricing_sku_costs")

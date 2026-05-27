"""产品表扩展字段 + BOM 冗余名称列

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # products: 新增 11 列
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("sub_name",         sa.String(255),  nullable=True))
        batch_op.add_column(sa.Column("listing_status",   sa.String(32),   nullable=True))
        batch_op.add_column(sa.Column("main_material",    sa.String(500),  nullable=True))
        batch_op.add_column(sa.Column("taobao_sku_id",    sa.String(64),   nullable=True))
        batch_op.add_column(sa.Column("accessory_desc",   sa.String(500),  nullable=True))
        batch_op.add_column(sa.Column("accessory_remark", sa.String(500),  nullable=True))
        batch_op.add_column(sa.Column("size_value",       sa.String(64),   nullable=True))
        batch_op.add_column(sa.Column("size_confirmed",   sa.String(32),   nullable=True))
        batch_op.add_column(sa.Column("sku",              sa.String(255),  nullable=True))
        batch_op.add_column(sa.Column("sku_code",         sa.String(32),   nullable=True))
        batch_op.create_index("ix_products_sku_code", ["sku_code"])

    # bom_lines: 新增 2 列
    with op.batch_alter_table("bom_lines") as batch_op:
        batch_op.add_column(sa.Column("product_name",  sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("material_name", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bom_lines") as batch_op:
        batch_op.drop_column("material_name")
        batch_op.drop_column("product_name")

    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_index("ix_products_sku_code")
        batch_op.drop_column("sku_code")
        batch_op.drop_column("sku")
        batch_op.drop_column("size_confirmed")
        batch_op.drop_column("size_value")
        batch_op.drop_column("accessory_remark")
        batch_op.drop_column("accessory_desc")
        batch_op.drop_column("taobao_sku_id")
        batch_op.drop_column("main_material")
        batch_op.drop_column("listing_status")
        batch_op.drop_column("sub_name")

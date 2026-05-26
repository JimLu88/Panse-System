"""initial Phase 1 schema: materials, products, bom_lines, inventory, data_exceptions

Revision ID: 0001
Revises:
Create Date: 2026-05-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "materials",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("size_type", sa.String(32)),
        sa.Column("unit", sa.String(16)),
        sa.Column("price", sa.Numeric(12, 2)),
        sa.Column("remark", sa.String(255)),
        sa.Column("is_custom", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_materials_code", "materials", ["code"])
    op.create_index("ix_materials_name_unique", "materials", ["name"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(32)),
        sa.Column("category", sa.String(64)),
        sa.Column("remark", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_products_code", "products", ["code"])

    op.create_table(
        "bom_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_code", sa.String(32), nullable=False),
        sa.Column("sku", sa.String(255)),
        sa.Column("sku_code", sa.String(32)),
        sa.Column("material_code", sa.String(32), sa.ForeignKey("materials.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit", sa.String(16)),
        sa.Column("qty_per_product", sa.Numeric(12, 4), server_default="1"),
        sa.Column("remark", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_bom_lines_product_code", "bom_lines", ["product_code"])
    op.create_index("ix_bom_lines_sku_code", "bom_lines", ["sku_code"])
    op.create_index("ix_bom_lines_material_code", "bom_lines", ["material_code"])

    op.create_table(
        "part_inventory",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("warehouse", sa.String(64), nullable=False),
        sa.Column("material_code", sa.String(32), sa.ForeignKey("materials.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("spec", sa.String(255)),
        sa.Column("unit", sa.String(16)),
        sa.Column("physical_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_inbound_at", sa.Date),
        sa.Column("last_outbound_at", sa.Date),
        sa.Column("safety_stock", sa.Integer),
        sa.Column("remark", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_part_inventory_warehouse", "part_inventory", ["warehouse"])
    op.create_index("ix_part_inventory_material_code", "part_inventory", ["material_code"])

    op.create_table(
        "product_inventory",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("warehouse", sa.String(64), nullable=False),
        sa.Column("product_code", sa.String(32), nullable=False),
        sa.Column("sku", sa.String(255)),
        sa.Column("spec", sa.String(255)),
        sa.Column("unit", sa.String(16)),
        sa.Column("physical_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("remark", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_product_inventory_warehouse", "product_inventory", ["warehouse"])
    op.create_index("ix_product_inventory_product_code", "product_inventory", ["product_code"])

    op.create_table(
        "data_exceptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_table", sa.String(64), nullable=False),
        sa.Column("source_pk", sa.String(64)),
        sa.Column("exception_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("suggestion_action", sa.String(64)),
        sa.Column("context", sa.JSON),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolved_by", sa.String(64)),
        sa.Column("resolved_at", sa.String(32)),
        sa.Column("ai_analysis", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_data_exceptions_source_table", "data_exceptions", ["source_table"])
    op.create_index("ix_data_exceptions_source_pk", "data_exceptions", ["source_pk"])
    op.create_index("ix_data_exceptions_exception_type", "data_exceptions", ["exception_type"])
    op.create_index("ix_data_exceptions_status", "data_exceptions", ["status"])


def downgrade() -> None:
    op.drop_table("data_exceptions")
    op.drop_table("product_inventory")
    op.drop_table("part_inventory")
    op.drop_table("bom_lines")
    op.drop_table("products")
    op.drop_table("materials")

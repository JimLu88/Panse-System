"""custom_variants 表 + products.taobao_id 备选 ID + refill.commission

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. products 加淘宝 ID + 备选
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("taobao_id", sa.String(32)))
        batch.add_column(sa.Column("alt_taobao_ids", sa.JSON))
    op.create_index("ix_products_taobao_id", "products", ["taobao_id"])

    # 1b. bom_lines 加 size_type (业务需求 §2)
    with op.batch_alter_table("bom_lines") as batch:
        batch.add_column(sa.Column("size_type", sa.String(32)))

    # 2. refill_records 加佣金
    with op.batch_alter_table("refill_records") as batch:
        batch.add_column(sa.Column("commission", sa.Numeric(12, 2)))

    # 3. 新建 custom_variants 表 (业务需求 §2)
    op.create_table(
        "custom_variants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("base_sku_code", sa.String(32), nullable=False),
        sa.Column("custom_sku_code", sa.String(48), nullable=False, unique=True),
        sa.Column("related_order_no", sa.String(64)),
        sa.Column("product_code", sa.String(32)),
        sa.Column("dimension_overrides", sa.JSON),
        sa.Column("bom_overrides", sa.JSON),
        sa.Column("auto_created_materials", sa.JSON),
        sa.Column("note", sa.Text),
        sa.Column("created_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_custom_variants_base_sku", "custom_variants", ["base_sku_code"])
    op.create_index("ix_custom_variants_custom_sku", "custom_variants", ["custom_sku_code"])
    op.create_index("ix_custom_variants_order_no", "custom_variants", ["related_order_no"])
    op.create_index("ix_custom_variants_product_code", "custom_variants", ["product_code"])


def downgrade() -> None:
    op.drop_table("custom_variants")
    with op.batch_alter_table("refill_records") as batch:
        batch.drop_column("commission")
    op.drop_index("ix_products_taobao_id", table_name="products")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("alt_taobao_ids")
        batch.drop_column("taobao_id")

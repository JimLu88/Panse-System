"""materials 加 category (配件分类; 用户 2026-06-26, 方向1)。

189 个 AC 配件原本全平铺无分类 → 加 category 字段(用户自定义文字: 五金/玻璃/岩板/洞石饰面板/
电力轨道/铝合金槽/杂项/床铺板/软包…)。配件库 UI 管理分类; 大宗材料对账改由 Material.category +
BOM 驱动分组(替代硬编码关键词登记表)。单值、可自定义、默认空。

Revision ID: 0097
Revises: 0096
"""
import sqlalchemy as sa
from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("materials", sa.Column("category", sa.String(64), nullable=True))
    op.create_index("ix_materials_category", "materials", ["category"])


def downgrade() -> None:
    op.drop_index("ix_materials_category", table_name="materials")
    op.drop_column("materials", "category")

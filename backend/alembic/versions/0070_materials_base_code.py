"""materials: base_material_code (Plan C3 定制防串料 — 定制件记基础物料码, 复用判定精确对照)。

幂等: 列已存在则跳过。

Revision ID: 0070
Revises: 0069
"""
import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("materials")}
    if "base_material_code" not in cols:
        op.add_column(
            "materials",
            sa.Column("base_material_code", sa.String(length=64), nullable=True),
        )
        op.create_index(
            "ix_materials_base_material_code", "materials", ["base_material_code"],
        )


def downgrade() -> None:
    op.drop_index("ix_materials_base_material_code", table_name="materials")
    op.drop_column("materials", "base_material_code")

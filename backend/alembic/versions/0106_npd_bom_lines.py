"""NPD P2a: 设计 BOM (npd_bom_lines) — 设计落地自动建产品/物料/定价 的来源。

Revision ID: 0106
Revises: 0105
"""
import sqlalchemy as sa
from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "npd_bom_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_code", sa.String(32), nullable=True),
        sa.Column("material_name", sa.String(255), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False, server_default=sa.text("1")),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("size_type", sa.String(32), nullable=True),
        sa.Column("is_new", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_bom_lines_project_id", "npd_bom_lines", ["project_id"])


def downgrade() -> None:
    op.drop_table("npd_bom_lines")

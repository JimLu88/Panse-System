"""NPD P1b: 验收检验项模板 + 实例 (npd_inspection_templates / npd_inspection_items)。

模板 startup 幂等种入; 进打样/验收阶段(S13/S15/S17)自动 instantiate; 必检项全 pass 才过门。

Revision ID: 0104
Revises: 0103
"""
import sqlalchemy as sa
from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "npd_inspection_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stage_code", sa.String(8), nullable=False),
        sa.Column("item_name", sa.String(128), nullable=False),
        sa.Column("check_type", sa.String(16), nullable=False, server_default=sa.text("'pass'")),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("min_val", sa.Numeric(12, 3), nullable=True),
        sa.Column("max_val", sa.Numeric(12, 3), nullable=True),
        sa.Column("expected", sa.String(64), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_npd_inspection_templates_stage_code", "npd_inspection_templates", ["stage_code"])

    op.create_table(
        "npd_inspection_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_instance_id", sa.Integer(),
                  sa.ForeignKey("npd_stage_instances.id", ondelete="CASCADE"), nullable=True),
        sa.Column("stage_code", sa.String(8), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("item_name", sa.String(128), nullable=False),
        sa.Column("check_type", sa.String(16), nullable=False, server_default=sa.text("'pass'")),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("min_val", sa.Numeric(12, 3), nullable=True),
        sa.Column("max_val", sa.Numeric(12, 3), nullable=True),
        sa.Column("expected", sa.String(64), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("reading", sa.String(128), nullable=True),
        sa.Column("result", sa.String(8), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_inspection_items_project_id", "npd_inspection_items", ["project_id"])
    op.create_index("ix_npd_inspection_items_stage_instance_id", "npd_inspection_items", ["stage_instance_id"])
    op.create_index("ix_npd_inspection_items_stage_code", "npd_inspection_items", ["stage_code"])


def downgrade() -> None:
    op.drop_table("npd_inspection_items")
    op.drop_table("npd_inspection_templates")

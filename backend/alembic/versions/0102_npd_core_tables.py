"""新品开发(NPD)板块 P0 三表: npd_stages / npd_projects / npd_stage_instances。

阶段定义在 startup 由 npd_service.seed_stages 幂等种入(24阶段+5门, 量产组 requires_mass_production)。

Revision ID: 0102
Revises: 0101
"""
import sqlalchemy as sa
from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "npd_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("group", sa.String(16), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column("is_gate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("allow_release", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("requires_mass_production", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("default_sla_days", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("warn_days", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("critical_days", sa.Integer(), nullable=False, server_default=sa.text("2")),
    )
    op.create_index("ix_npd_stages_code", "npd_stages", ["code"], unique=True)
    op.create_index("ix_npd_stages_group", "npd_stages", ["group"])
    op.create_index("ix_npd_stages_sequence", "npd_stages", ["sequence"])

    op.create_table(
        "npd_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("brand", sa.String(32), nullable=True),
        sa.Column("product_line", sa.String(64), nullable=True),
        sa.Column("current_stage_id", sa.Integer(),
                  sa.ForeignKey("npd_stages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("kanban_state", sa.String(16), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("owner", sa.String(64), nullable=True),
        sa.Column("priority", sa.String(8), nullable=False, server_default=sa.text("'mid'")),
        sa.Column("target_launch_date", sa.Date(), nullable=True),
        sa.Column("percent_done", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("target_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("target_margin_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("product_code", sa.String(32), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_projects_code", "npd_projects", ["code"], unique=True)
    op.create_index("ix_npd_projects_category", "npd_projects", ["category"])
    op.create_index("ix_npd_projects_product_line", "npd_projects", ["product_line"])
    op.create_index("ix_npd_projects_current_stage_id", "npd_projects", ["current_stage_id"])
    op.create_index("ix_npd_projects_state", "npd_projects", ["state"])
    op.create_index("ix_npd_projects_owner", "npd_projects", ["owner"])
    op.create_index("ix_npd_projects_product_code", "npd_projects", ["product_code"])

    op.create_table(
        "npd_stage_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_id", sa.Integer(),
                  sa.ForeignKey("npd_stages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alert_level", sa.String(8), nullable=True),
        sa.Column("alert_reason", sa.String(255), nullable=True),
        sa.Column("gate_result", sa.String(8), nullable=True),
        sa.Column("gate_decided_by", sa.String(64), nullable=True),
        sa.Column("gate_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_stage_instances_project_id", "npd_stage_instances", ["project_id"])
    op.create_index("ix_npd_stage_instances_stage_id", "npd_stage_instances", ["stage_id"])


def downgrade() -> None:
    op.drop_table("npd_stage_instances")
    op.drop_table("npd_projects")
    op.drop_table("npd_stages")

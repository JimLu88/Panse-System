"""NPD P1a: 阶段待办模板 + 任务实例 (npd_stage_task_templates / npd_tasks)。

模板由 startup npd_service.seed_task_templates 幂等种入; 项目进阶段自动 instantiate 任务。

Revision ID: 0103
Revises: 0102
"""
import sqlalchemy as sa
from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "npd_stage_task_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stage_code", sa.String(8), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("category", sa.String(16), nullable=False, server_default=sa.text("'通用'")),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("offset_days", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_npd_stage_task_templates_stage_code", "npd_stage_task_templates", ["stage_code"])

    op.create_table(
        "npd_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_instance_id", sa.Integer(),
                  sa.ForeignKey("npd_stage_instances.id", ondelete="CASCADE"), nullable=True),
        sa.Column("stage_code", sa.String(8), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("category", sa.String(16), nullable=False, server_default=sa.text("'通用'")),
        sa.Column("assignee", sa.String(64), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'open'")),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by", sa.String(64), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_tasks_project_id", "npd_tasks", ["project_id"])
    op.create_index("ix_npd_tasks_stage_instance_id", "npd_tasks", ["stage_instance_id"])
    op.create_index("ix_npd_tasks_stage_code", "npd_tasks", ["stage_code"])


def downgrade() -> None:
    op.drop_table("npd_tasks")
    op.drop_table("npd_stage_task_templates")

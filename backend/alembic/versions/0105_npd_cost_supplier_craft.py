"""NPD P1c: 成本门 + 工艺问题台账 + 供应商候选。

npd_cost_gates / npd_craft_issues / npd_supplier_candidates。

Revision ID: 0105
Revises: 0104
"""
import sqlalchemy as sa
from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "npd_cost_gates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prototype_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("est_mass_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("target_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("target_margin", sa.Numeric(5, 4), nullable=True),
        sa.Column("actual_margin", sa.Numeric(6, 4), nullable=True),
        sa.Column("verdict", sa.String(8), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_cost_gates_project_id", "npd_cost_gates", ["project_id"], unique=True)

    op.create_table(
        "npd_craft_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_code", sa.String(8), nullable=True),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("cost_impact", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(8), nullable=False, server_default=sa.text("'open'")),
        sa.Column("chosen_supplier", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_craft_issues_project_id", "npd_craft_issues", ["project_id"])

    op.create_table(
        "npd_supplier_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_category", sa.String(64), nullable=True),
        sa.Column("supplier_name", sa.String(128), nullable=False),
        sa.Column("is_backup", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("quote_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("quote_status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("can_solve_craft_issue", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("craft_solution", sa.Text(), nullable=True),
        sa.Column("solved_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_supplier_candidates_project_id", "npd_supplier_candidates", ["project_id"])


def downgrade() -> None:
    op.drop_table("npd_supplier_candidates")
    op.drop_table("npd_craft_issues")
    op.drop_table("npd_cost_gates")

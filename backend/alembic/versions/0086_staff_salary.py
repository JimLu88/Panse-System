"""staff_salaries 新表 — 人员/工资管理 (G: 自由增减人员、改月工资)。

外包成本口径挂钩: order_financials.outsourcing_for_range 每月预估额
从写死 ¥10000 改为 Σ 当月在职人员 monthly_cost。

Revision ID: 0086
Revises: 0085
"""
import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "staff_salaries" in insp.get_table_names():
        return
    op.create_table(
        "staff_salaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("monthly_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("active_from", sa.Date(), nullable=False),
        sa.Column("active_to", sa.Date(), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_staff_salaries_active_from", "staff_salaries", ["active_from"])


def downgrade() -> None:
    try:
        op.drop_index("ix_staff_salaries_active_from", table_name="staff_salaries")
        op.drop_table("staff_salaries")
    except Exception:  # noqa: BLE001
        pass

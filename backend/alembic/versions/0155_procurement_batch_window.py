"""Add opt-in fixed procurement windows without starting legacy tasks.

Revision ID: 0155
Revises: 0154
"""
from alembic import op
import sqlalchemy as sa

revision = "0155"
down_revision = "0154"
branch_labels = None
depends_on = None


def upgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("procurement_tasks")}
    for name, kind in (
        ("batch_policy_version", sa.String(24)), ("started_at", sa.DateTime(timezone=True)),
        ("deadline_at", sa.DateTime(timezone=True)), ("closed_at", sa.DateTime(timezone=True)),
        ("policy_snapshot", sa.JSON()), ("deadline_report", sa.JSON()),
    ):
        if name not in existing:
            op.add_column("procurement_tasks", sa.Column(name, kind, nullable=True))
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("procurement_tasks")}
    if "ix_procurement_tasks_deadline_at" not in indexes:
        op.create_index("ix_procurement_tasks_deadline_at", "procurement_tasks", ["deadline_at"])


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM procurement_tasks WHERE batch_policy_version IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("已有新批次证据，禁止丢失截止时间或回退给旧执行器；请保留数据并停发")
    op.drop_index("ix_procurement_tasks_deadline_at", table_name="procurement_tasks")
    for name in ("deadline_report", "policy_snapshot", "closed_at", "deadline_at", "started_at", "batch_policy_version"):
        op.drop_column("procurement_tasks", name)

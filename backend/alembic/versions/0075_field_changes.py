"""field_changes 新表 (人工编辑历史档案 — 字段级修改流水, 方向2+4)。

幂等: 表已存在则跳过。

Revision ID: 0075
Revises: 0074
"""
import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "field_changes" in insp.get_table_names():
        return
    op.create_table(
        "field_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("row_pk", sa.String(length=64), nullable=False),
        sa.Column("row_label", sa.String(length=255), nullable=True),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("field_label", sa.String(length=64), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="web"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_field_changes_target", "field_changes", ["table_name", "row_pk", "field"])
    op.create_index("ix_field_changes_created", "field_changes", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_field_changes_created", table_name="field_changes")
    op.drop_index("ix_field_changes_target", table_name="field_changes")
    op.drop_table("field_changes")

"""disassembly_logs 新表 (拆 BOM 历史 + 回撤, 用户需求 2026-06-11)。

Revision ID: 0077
Revises: 0076
"""
import sqlalchemy as sa
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "disassembly_logs" in insp.get_table_names():
        return
    op.create_table(
        "disassembly_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_code", sa.String(length=64), nullable=False),
        sa.Column("sku_code", sa.String(length=64), nullable=True),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("parts_json", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_disassembly_logs_created", "disassembly_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_disassembly_logs_created", table_name="disassembly_logs")
    op.drop_table("disassembly_logs")

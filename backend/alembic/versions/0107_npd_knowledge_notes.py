"""NPD P2b: 设计知识库笔记 (npd_knowledge_notes)。

Revision ID: 0107
Revises: 0106
"""
import sqlalchemy as sa
from alembic import op

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "npd_knowledge_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("material", sa.String(128), nullable=True),
        sa.Column("tags", sa.String(255), nullable=True),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("source_project_id", sa.Integer(), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_npd_knowledge_notes_category", "npd_knowledge_notes", ["category"])
    op.create_index("ix_npd_knowledge_notes_material", "npd_knowledge_notes", ["material"])


def downgrade() -> None:
    op.drop_table("npd_knowledge_notes")

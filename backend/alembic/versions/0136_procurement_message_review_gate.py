"""Add mandatory human review gates for procurement messages.

Revision ID: 0136
Revises: 0135
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0136"
down_revision = "0135"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_tasks" in tables:
        columns = _columns("procurement_tasks")
        additions = (
            ("script_a_ai_draft", sa.Text()),
            ("script_b_ai_draft", sa.Text()),
            ("scripts_reviewed_at", sa.DateTime(timezone=True)),
            ("scripts_reviewed_by", sa.String(length=64)),
        )
        for name, column_type in additions:
            if name not in columns:
                op.add_column("procurement_tasks", sa.Column(name, column_type))

    if "procurement_inquiries" in tables:
        columns = _columns("procurement_inquiries")
        additions = (
            ("approved_message", sa.Text()),
            ("approved_message_base", sa.Text()),
            ("approved_action_key", sa.String(length=64)),
            ("message_reviewed_at", sa.DateTime(timezone=True)),
            ("message_reviewed_by", sa.String(length=64)),
        )
        for name, column_type in additions:
            if name not in columns:
                op.add_column("procurement_inquiries", sa.Column(name, column_type))
        op.create_index(
            "ix_procurement_inquiries_approved_action_key",
            "procurement_inquiries",
            ["approved_action_key"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "procurement_inquiries" in tables:
        columns = _columns("procurement_inquiries")
        if "approved_action_key" in columns:
            op.drop_index(
                "ix_procurement_inquiries_approved_action_key",
                table_name="procurement_inquiries",
            )
        for name in (
            "message_reviewed_by",
            "message_reviewed_at",
            "approved_action_key",
            "approved_message_base",
            "approved_message",
        ):
            if name in columns:
                op.drop_column("procurement_inquiries", name)

    if "procurement_tasks" in tables:
        columns = _columns("procurement_tasks")
        for name in (
            "scripts_reviewed_by",
            "scripts_reviewed_at",
            "script_b_ai_draft",
            "script_a_ai_draft",
        ):
            if name in columns:
                op.drop_column("procurement_tasks", name)

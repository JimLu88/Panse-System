"""Track Taobao remote-order reporting confirmation and daily reminders.

Revision ID: 0137
Revises: 0136
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa


revision = "0137"
down_revision = "0136"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("orders")
    additions = (
        (
            "taobao_remote_report_required",
            sa.Column(
                "taobao_remote_report_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        ),
        (
            "taobao_remote_report_confirmed_at",
            sa.Column("taobao_remote_report_confirmed_at", sa.DateTime(timezone=True)),
        ),
        (
            "taobao_remote_report_last_prompt_at",
            sa.Column("taobao_remote_report_last_prompt_at", sa.DateTime(timezone=True)),
        ),
        (
            "taobao_remote_report_keyword",
            sa.Column("taobao_remote_report_keyword", sa.String(length=128)),
        ),
        (
            "taobao_remote_report_card_message_id",
            sa.Column("taobao_remote_report_card_message_id", sa.String(length=128)),
        ),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("orders", column)


def downgrade() -> None:
    columns = _columns("orders")
    for name in (
        "taobao_remote_report_card_message_id",
        "taobao_remote_report_keyword",
        "taobao_remote_report_last_prompt_at",
        "taobao_remote_report_confirmed_at",
        "taobao_remote_report_required",
    ):
        if name in columns:
            op.drop_column("orders", name)

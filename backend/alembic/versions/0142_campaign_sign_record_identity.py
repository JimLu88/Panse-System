"""Add exact enrolled-record identity for read-only campaign exports.

Revision ID: 0142
Revises: 0141
Create Date: 2026-08-28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0142"
down_revision = "0141"
branch_labels = None
depends_on = None

_TABLE = "campaign_plans"
_COLUMN = "platform_sign_record_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if _COLUMN not in columns:
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if _COLUMN in columns:
        op.drop_column(_TABLE, _COLUMN)

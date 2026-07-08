"""order.parts_override JSON — 逐单配件覆盖 (追加/补差单人工指定配件, 治双计 BOM)

Revision ID: 0123
Revises: 0122
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    from sqlalchemy import inspect
    return any(c["name"] == col for c in inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column("orders", "parts_override"):
        op.add_column("orders", sa.Column("parts_override", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column("orders", "parts_override"):
        op.drop_column("orders", "parts_override")

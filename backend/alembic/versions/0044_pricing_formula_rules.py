"""pricing_formula_rules — configurable formula engine for computed columns.

Revision ID: 0044
Revises: 0043
"""
from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pricing_formula_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("field_name", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("expression", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, default=True, server_default="true"),
        sa.Column("sort_order", sa.Integer, nullable=False, default=0, server_default="0"),
        sa.Column("is_builtin", sa.Boolean, nullable=False, default=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_pricing_formula_rules_sort", "pricing_formula_rules", ["sort_order"])


def downgrade():
    op.drop_table("pricing_formula_rules")

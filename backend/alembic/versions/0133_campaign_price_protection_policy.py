"""活动计划增加价保规则链接与可调冷静期。

Revision ID: 0133
Revises: 0132
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0133"
down_revision = "0132"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "campaign_plans" not in sa.inspect(op.get_bind()).get_table_names():
        return
    columns = _columns("campaign_plans")
    if "price_protection_days" not in columns:
        op.add_column(
            "campaign_plans",
            sa.Column("price_protection_days", sa.Integer(), nullable=False, server_default="19"),
        )
    if "price_protection_rule_url" not in columns:
        op.add_column(
            "campaign_plans",
            sa.Column("price_protection_rule_url", sa.String(length=1024), nullable=True),
        )
    if "price_protection_confirmed_at" not in columns:
        op.add_column(
            "campaign_plans",
            sa.Column("price_protection_confirmed_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if "campaign_plans" not in sa.inspect(op.get_bind()).get_table_names():
        return
    columns = _columns("campaign_plans")
    for name in (
        "price_protection_confirmed_at",
        "price_protection_rule_url",
        "price_protection_days",
    ):
        if name in columns:
            op.drop_column("campaign_plans", name)

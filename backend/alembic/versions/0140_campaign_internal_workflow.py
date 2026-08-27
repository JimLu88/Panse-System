"""Formal campaign internal workflow, whole-item exclusions, registry cleanup.

Revision ID: 0140
Revises: 0139
Create Date: 2026-08-28
"""
from __future__ import annotations

import json
import re

from alembic import op
import sqlalchemy as sa


revision = "0140"
down_revision = "0139"
branch_labels = None
depends_on = None

_ITEM_ID_RE = re.compile(r"\d{4,20}")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _sanitize_no_sales_registry() -> None:
    if "system_settings" not in _tables():
        return
    bind = op.get_bind()
    table = sa.table(
        "system_settings",
        sa.column("key", sa.String()),
        sa.column("value_plain", sa.Text()),
        sa.column("is_secret", sa.Boolean()),
    )
    row = bind.execute(
        sa.select(table.c.value_plain, table.c.is_secret).where(
            table.c.key == "no_sales_item_ids")
    ).first()
    if row is None or row[1]:
        return
    parse_error = False
    try:
        parsed = json.loads(row[0]) if row[0] else []
    except (TypeError, ValueError):
        parsed = []
        parse_error = bool(row[0])
    values = parsed if isinstance(parsed, list) else []
    clean = sorted({
        text for value in values
        if (text := str(value or "").strip()) and _ITEM_ID_RE.fullmatch(text)
    })
    if parse_error or not isinstance(parsed, list) or clean != values:
        bind.execute(
            table.update().where(table.c.key == "no_sales_item_ids").values(
                value_plain=json.dumps(clean, ensure_ascii=False)
            )
        )


def upgrade() -> None:
    if "campaign_plans" in _tables():
        columns = _columns("campaign_plans")
        additions = (
            ("workflow_key", sa.Column("workflow_key", sa.String(128))),
            ("platform_activity_mode", sa.Column(
                "platform_activity_mode", sa.String(32), nullable=False,
                server_default="fixed_window")),
            ("platform_campaign_id", sa.Column("platform_campaign_id", sa.String(64))),
            ("platform_united_activity_id", sa.Column(
                "platform_united_activity_id", sa.String(64))),
            ("platform_active_until", sa.Column("platform_active_until", sa.DateTime())),
        )
        for name, column in additions:
            if name not in columns:
                op.add_column("campaign_plans", column)
        indexes = _indexes("campaign_plans")
        if "ix_campaign_plans_workflow_key" not in indexes:
            op.create_index(
                "ix_campaign_plans_workflow_key", "campaign_plans",
                ["workflow_key"], unique=True)

    if "campaign_item_exclusions" not in _tables():
        op.create_table(
            "campaign_item_exclusions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("taobao_item_id", sa.String(64), nullable=False),
            sa.Column("reason", sa.String(255), nullable=False),
            sa.Column("source", sa.String(64), nullable=False,
                      server_default="operator_confirmed"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index(
            "ix_campaign_item_exclusions_taobao_item_id",
            "campaign_item_exclusions", ["taobao_item_id"], unique=True)

    _sanitize_no_sales_registry()


def downgrade() -> None:
    if "campaign_item_exclusions" in _tables():
        op.drop_table("campaign_item_exclusions")
    if "campaign_plans" in _tables():
        indexes = _indexes("campaign_plans")
        if "ix_campaign_plans_workflow_key" in indexes:
            op.drop_index("ix_campaign_plans_workflow_key", table_name="campaign_plans")
        columns = _columns("campaign_plans")
        for name in (
            "platform_active_until", "platform_united_activity_id",
            "platform_campaign_id", "platform_activity_mode", "workflow_key",
        ):
            if name in columns:
                op.drop_column("campaign_plans", name)

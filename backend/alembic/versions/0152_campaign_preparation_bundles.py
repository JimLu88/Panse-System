"""Persist immutable campaign pre-submit preparation bundles.

Revision ID: 0152
Revises: 0151
Create Date: 2026-09-03
"""
from __future__ import annotations

import secrets

from alembic import op
import sqlalchemy as sa

from app.services import settings_service


revision = "0152"
down_revision = "0151"
branch_labels = None
depends_on = None

_TOKEN_KEY = "campaign_preparation_bundle_service_token"


def upgrade() -> None:
    op.create_table(
        "campaign_preparation_bundles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("workflow_key", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("prepared_by", sa.String(length=128), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("identity", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("signup_rows", sa.JSON(), nullable=False),
        sa.Column("discount_rows", sa.JSON(), nullable=False),
        sa.Column("item_decisions", sa.JSON(), nullable=False),
        sa.Column("gate_results", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("execution_boundary", sa.JSON(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_attempt_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_key", "source_sha256",
            name="uq_campaign_preparation_workflow_source"),
        sa.UniqueConstraint(
            "workflow_key", "revision",
            name="uq_campaign_preparation_workflow_revision"),
    )
    op.create_index(
        "ix_campaign_preparation_bundles_plan_id",
        "campaign_preparation_bundles", ["plan_id"], unique=False)
    op.create_index(
        "ix_campaign_preparation_bundles_workflow_key",
        "campaign_preparation_bundles", ["workflow_key"], unique=False)
    op.create_index(
        "ix_campaign_preparation_bundles_state",
        "campaign_preparation_bundles", ["state"], unique=False)
    bind = op.get_bind()
    if "system_settings" not in sa.inspect(bind).get_table_names():
        return
    table = sa.table(
        "system_settings",
        sa.column("key", sa.String()),
        sa.column("value_plain", sa.Text()),
        sa.column("value_encrypted", sa.Text()),
        sa.column("is_secret", sa.Boolean()),
        sa.column("description", sa.String()),
    )
    existing = bind.execute(
        sa.select(table.c.key).where(table.c.key == _TOKEN_KEY)
    ).first()
    if existing is None:
        bind.execute(table.insert().values(
            key=_TOKEN_KEY,
            value_plain=None,
            value_encrypted=settings_service.encrypt(secrets.token_urlsafe(48)),
            is_secret=True,
            description=(
                "Campaign immutable preparation bundle: exact read-only "
                "endpoint only; never returned by the API"),
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if "system_settings" in sa.inspect(bind).get_table_names():
        table = sa.table(
            "system_settings", sa.column("key", sa.String()))
        bind.execute(table.delete().where(table.c.key == _TOKEN_KEY))
    op.drop_index(
        "ix_campaign_preparation_bundles_state",
        table_name="campaign_preparation_bundles")
    op.drop_index(
        "ix_campaign_preparation_bundles_workflow_key",
        table_name="campaign_preparation_bundles")
    op.drop_index(
        "ix_campaign_preparation_bundles_plan_id",
        table_name="campaign_preparation_bundles")
    op.drop_table("campaign_preparation_bundles")

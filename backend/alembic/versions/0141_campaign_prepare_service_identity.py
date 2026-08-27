"""Provision an encrypted prepare-only campaign service identity.

Revision ID: 0141
Revises: 0140
Create Date: 2026-08-28
"""
from __future__ import annotations

import secrets

from alembic import op
import sqlalchemy as sa

from app.services import settings_service


revision = "0141"
down_revision = "0140"
branch_labels = None
depends_on = None

_KEY = "campaign_prepare_service_token"


def upgrade() -> None:
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
    row = bind.execute(
        sa.select(
            table.c.value_plain, table.c.value_encrypted, table.c.is_secret
        ).where(table.c.key == _KEY)
    ).first()
    if row is None:
        token = secrets.token_urlsafe(48)
        bind.execute(table.insert().values(
            key=_KEY,
            value_plain=None,
            value_encrypted=settings_service.encrypt(token),
            is_secret=True,
            description=(
                "01 activity executor: POST /api/campaigns/prepare only; "
                "consumed by the container CLI and never returned"
            ),
        ))
        return
    # Preserve any operator-provisioned value but ensure it is encrypted.
    if row.is_secret and row.value_encrypted:
        return
    value = str(row.value_plain or "").strip() or secrets.token_urlsafe(48)
    bind.execute(table.update().where(table.c.key == _KEY).values(
        value_plain=None,
        value_encrypted=settings_service.encrypt(value),
        is_secret=True,
        description=(
            "01 activity executor: POST /api/campaigns/prepare only; "
            "consumed by the container CLI and never returned"
        ),
    ))


def downgrade() -> None:
    bind = op.get_bind()
    if "system_settings" not in sa.inspect(bind).get_table_names():
        return
    table = sa.table(
        "system_settings",
        sa.column("key", sa.String()),
    )
    bind.execute(table.delete().where(table.c.key == _KEY))

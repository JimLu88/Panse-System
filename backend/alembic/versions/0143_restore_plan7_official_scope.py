"""Restore the operator-owned official scope for Super Reduce plan 7.

Revision ID: 0143
Revises: 0142
Create Date: 2026-08-30
"""
from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "0143"
down_revision = "0142"
branch_labels = None
depends_on = None

_TABLE = "campaign_plans"
_WORKFLOW = "campaign:super-reduce:2026-09-01"
_EXEMPT_ITEM = "805268708396"


def _remove_marker(text: str, key: str) -> str:
    pattern = rf"(?:^|[;\n；])\s*{re.escape(key)}\s*=\s*[^;\n；]*"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip(" ;\n；")


def _set_marker(text: str, key: str, value: str) -> str:
    text = _remove_marker(text, key)
    return f"{text}; {key}={value}" if text else f"{key}={value}"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if not {"workflow_key", "remark"}.issubset(columns):
        return

    row = bind.execute(
        sa.text(
            "SELECT id, remark FROM campaign_plans "
            "WHERE workflow_key = :workflow_key"
        ),
        {"workflow_key": _WORKFLOW},
    ).mappings().first()
    if not row:
        return

    remark = str(row.get("remark") or "")
    remark = _remove_marker(remark, "official_active_items")
    remark = _set_marker(remark, "official_all_store", "true")
    remark = _set_marker(remark, "official_exempt_items", _EXEMPT_ITEM)
    bind.execute(
        sa.text("UPDATE campaign_plans SET remark = :remark WHERE id = :plan_id"),
        {"remark": remark, "plan_id": row["id"]},
    )


def downgrade() -> None:
    # The migration restores an already approved plan-scoped safety exclusion.
    # Removing it on downgrade could re-enable a product, so rollback is a no-op.
    pass

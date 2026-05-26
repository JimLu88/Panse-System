"""suppliers.alipay_counterparty_keywords + alipay_account

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("alipay_counterparty_keywords", sa.JSON))
    op.add_column("suppliers", sa.Column("alipay_account", sa.String(32)))


def downgrade() -> None:
    op.drop_column("suppliers", "alipay_account")
    op.drop_column("suppliers", "alipay_counterparty_keywords")

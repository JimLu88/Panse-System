"""给 orders 加 custom_surcharge 定制加价列 (方案B).

is_custom 订单理论成本 = 基础BOM成本 + custom_surcharge; 加价可由定制报价单回填或手填。
nullable、向后兼容; 幂等 (列已存在则跳过), Postgres 与 SQLite 通用。

Revision ID: 0053
Revises: 0052
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    return col in {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("orders", "custom_surcharge"):
        op.add_column("orders", sa.Column("custom_surcharge", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    if _has_column("orders", "custom_surcharge"):
        op.drop_column("orders", "custom_surcharge")

"""给 part_inventory 加 defective_qty 待返厂/维修中数量列 (方案B 坏件闭环).

坏件从良品库移出, 计入 defective_qty (不进可用); 修好移回 physical, 报废/退货则核销。
nullable=False default 0, 向后兼容; 幂等 (列已存在则跳过), Postgres 与 SQLite 通用。

Revision ID: 0055
Revises: 0054
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def _has_column(table: str, col: str) -> bool:
    return col in {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("part_inventory", "defective_qty"):
        op.add_column(
            "part_inventory",
            sa.Column("defective_qty", sa.Numeric(14, 3),
                      nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if _has_column("part_inventory", "defective_qty"):
        op.drop_column("part_inventory", "defective_qty")

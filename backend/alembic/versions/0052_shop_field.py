"""给 taobao_listings 与 orders 加 shop 店铺列 (分店统计).

店铺值如 畔色店 / 孚格店。同一实物可跨店上架, 订单按店铺归属做分店统计。
全部 nullable、向后兼容; 幂等 (列/索引已存在则跳过), Postgres 与 SQLite 通用。

Revision ID: 0052
Revises: 0051
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

# (表, 列名) — 列类型统一 String(32), nullable; 索引名沿用 SQLAlchemy index=True 约定 ix_{表}_{列}
_TARGETS = [
    ("taobao_listings", "shop"),
    ("orders", "shop"),
]


def _cols(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {ix["name"] for ix in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    for table, col in _TARGETS:
        if col not in _cols(table):
            op.add_column(table, sa.Column(col, sa.String(length=32), nullable=True))
        idx = f"ix_{table}_{col}"
        if idx not in _indexes(table):
            op.create_index(idx, table, [col])


def downgrade() -> None:
    for table, col in reversed(_TARGETS):
        idx = f"ix_{table}_{col}"
        if idx in _indexes(table):
            op.drop_index(idx, table_name=table)
        if col in _cols(table):
            op.drop_column(table, col)

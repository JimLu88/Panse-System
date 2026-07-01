"""定价表 pricing_sku 加 成本加成基数 4 列 (base_list/base_small/base_mid/base_big)。

用户 2026-07-01 拍板对齐 Excel 定价法: 各档价 = ROUNDUP(会计基准 ÷ 基数, −1),
会计基准 = 物理成本 ÷ (1 − 2.6%)。基数逐 SKU/逐档不同, 由对齐脚本从 Excel 公式导入。
留空(NULL) → recompute 不走 cost-plus 推导, 保持原口径。纯新增列, 不动既有数据。

Revision ID: 0110
Revises: 0109
"""
import sqlalchemy as sa
from alembic import op

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None

_COLS = ("base_list", "base_small", "base_mid", "base_big")


def _has_column(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if "pricing_sku" not in sa.inspect(bind).get_table_names():
        return
    for col in _COLS:
        if not _has_column(bind, "pricing_sku", col):
            op.add_column("pricing_sku", sa.Column(col, sa.Numeric(6, 4), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "pricing_sku" not in sa.inspect(bind).get_table_names():
        return
    for col in _COLS:
        if _has_column(bind, "pricing_sku", col):
            op.drop_column("pricing_sku", col)

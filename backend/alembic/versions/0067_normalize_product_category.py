"""归一产品类目 (去重复): 餐厅-柜→餐厅-餐边柜, 餐厅-桌→餐厅-餐桌, 书房-书柜→书房-柜, 书房-桌→书房-书桌。

按用户要求合并重复类目。幂等: 只是按值改写, 重复跑无副作用。

Revision ID: 0067
Revises: 0066
"""
import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

# 旧值 → 规范值
_MAP = {
    "餐厅-柜": "餐厅-餐边柜",
    "餐厅-桌": "餐厅-餐桌",
    "书房-书柜": "书房-柜",
    "书房-桌": "书房-书桌",
}


def upgrade() -> None:
    bind = op.get_bind()
    for old, new in _MAP.items():
        bind.execute(
            sa.text("UPDATE products SET category = :new WHERE category = :old"),
            {"new": new, "old": old},
        )


def downgrade() -> None:
    # 归并不可逆(多个旧值并到一个), 不还原。
    pass

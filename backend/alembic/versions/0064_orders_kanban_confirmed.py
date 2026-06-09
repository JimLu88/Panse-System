"""orders.kanban_confirmed — 看板里人工拖拽"确定"过的订单标记 (区分人工已确定 vs 自动归类)。

幂等: 列已存在则跳过。

Revision ID: 0064
Revises: 0063
"""
import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "kanban_confirmed" in cols:
        return
    op.add_column(
        "orders",
        sa.Column("kanban_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("orders", "kanban_confirmed")

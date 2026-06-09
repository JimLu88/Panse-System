"""order_accessory_items: purchase_no(采购单号) + self_delivered(自送免物流)。

按配件聚合采购视图用: 记采购单号; 玻璃这类"工厂周边买+自送"标自送, 不需要物流号。
幂等: 列已存在则跳过。

Revision ID: 0065
Revises: 0064
"""
import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("order_accessory_items")}
    if "purchase_no" not in cols:
        op.add_column("order_accessory_items", sa.Column("purchase_no", sa.String(128), nullable=True))
    if "self_delivered" not in cols:
        op.add_column(
            "order_accessory_items",
            sa.Column("self_delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("order_accessory_items", "self_delivered")
    op.drop_column("order_accessory_items", "purchase_no")

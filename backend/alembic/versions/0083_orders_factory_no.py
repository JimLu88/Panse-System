"""orders 加工厂制单编号 factory_no (工厂按"畔色 X 单"下单; 历史读ZIP回填, 新单按下单序顺排)。

Revision ID: 0083
Revises: 0082
"""
import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "factory_no" not in cols:
        op.add_column("orders", sa.Column("factory_no", sa.Integer(), nullable=True))
        op.create_index("ix_orders_factory_no", "orders", ["factory_no"])


def downgrade() -> None:
    try:
        op.drop_index("ix_orders_factory_no", table_name="orders")
    except Exception:
        pass
    op.drop_column("orders", "factory_no")

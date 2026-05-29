"""订单表增 discount/platform_fee; 工厂下单表增 internal_order_no

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("discount",     sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("platform_fee", sa.Numeric(12, 2), nullable=True))

    with op.batch_alter_table("factory_orders") as batch_op:
        batch_op.add_column(sa.Column("internal_order_no", sa.String(32), nullable=True))
        batch_op.create_index("ix_factory_orders_internal_order_no", ["internal_order_no"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("factory_orders") as batch_op:
        batch_op.drop_index("ix_factory_orders_internal_order_no")
        batch_op.drop_column("internal_order_no")

    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("platform_fee")
        batch_op.drop_column("discount")

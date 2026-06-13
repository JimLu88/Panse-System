"""orders: is_remote_ship(远期单 — 等客户通知再发, 工厂制作单里单独归类)。

幂等: 列已存在则跳过。default False。

Revision ID: 0069
Revises: 0068
"""
import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "is_remote_ship" not in cols:
        op.add_column(
            "orders",
            sa.Column("is_remote_ship", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("orders", "is_remote_ship")

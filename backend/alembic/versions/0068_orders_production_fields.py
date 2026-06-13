"""orders: ship_deadline(手动发货截止) + production_note(工厂制作单卡片备注)。

工厂制作单视图用: 默认 下单日+30天 倒扣; 特殊单可手动改 ship_deadline; 卡片备注存 production_note。
幂等: 列已存在则跳过。

Revision ID: 0068
Revises: 0067
"""
import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "ship_deadline" not in cols:
        op.add_column("orders", sa.Column("ship_deadline", sa.Date(), nullable=True))
    if "production_note" not in cols:
        op.add_column("orders", sa.Column("production_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "production_note")
    op.drop_column("orders", "ship_deadline")

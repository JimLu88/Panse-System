"""代付台账 prepay_ledgers (补单佣金/补单快递/售后 的实际打款进项来源)。

幂等: 表已存在则跳过。

Revision ID: 0061
Revises: 0060
"""
import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "prepay_ledgers" in insp.get_table_names():
        return
    op.create_table(
        "prepay_ledgers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("pay_no", sa.String(64), nullable=True),
        sa.Column("order_no", sa.String(64), nullable=True),
        sa.Column("pay_date", sa.Date, nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("payee", sa.String(128), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="import"),
        sa.Column("remark", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_prepay_ledgers_category", "prepay_ledgers", ["category"])
    op.create_index("ix_prepay_ledgers_pay_no", "prepay_ledgers", ["pay_no"], unique=True)
    op.create_index("ix_prepay_ledgers_order_no", "prepay_ledgers", ["order_no"])
    op.create_index("ix_prepay_ledgers_pay_date", "prepay_ledgers", ["pay_date"])


def downgrade() -> None:
    op.drop_table("prepay_ledgers")

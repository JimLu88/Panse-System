"""木作工厂月结销账: factory_orders 加 settlement_month/settlement_payment_id
+ factory_settlement_payments(销账记录) + factory_supplier_aliases(供应商别名)。

Revision ID: 0108
Revises: 0107
"""
import sqlalchemy as sa
from alembic import op

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    # 1) factory_orders 加两列 (幂等)
    if _has_table(bind, "factory_orders"):
        if not _has_column(bind, "factory_orders", "settlement_month"):
            op.add_column("factory_orders", sa.Column("settlement_month", sa.String(7), nullable=True))
            op.create_index("ix_factory_orders_settlement_month", "factory_orders", ["settlement_month"])
        if not _has_column(bind, "factory_orders", "settlement_payment_id"):
            op.add_column("factory_orders", sa.Column("settlement_payment_id", sa.Integer(), nullable=True))
            op.create_index("ix_factory_orders_settlement_payment_id", "factory_orders", ["settlement_payment_id"])
    # 2) 销账记录表
    if not _has_table(bind, "factory_settlement_payments"):
        op.create_table(
            "factory_settlement_payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("supplier", sa.String(128), nullable=False),
            sa.Column("settlement_month", sa.String(7), nullable=False),
            sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
            sa.Column("alipay_flow_no", sa.String(128), nullable=True),
            sa.Column("paid_amount", sa.Numeric(14, 2), nullable=True),
            sa.Column("flipped_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reversed_by", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_factory_settlement_payments_supplier", "factory_settlement_payments", ["supplier"])
        op.create_index("ix_factory_settlement_payments_settlement_month", "factory_settlement_payments", ["settlement_month"])
    # 3) 供应商别名表
    if not _has_table(bind, "factory_supplier_aliases"):
        op.create_table(
            "factory_supplier_aliases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("supplier", sa.String(128), nullable=False),
            sa.Column("alias", sa.String(255), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_factory_supplier_aliases_supplier", "factory_supplier_aliases", ["supplier"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "factory_supplier_aliases"):
        op.drop_table("factory_supplier_aliases")
    if _has_table(bind, "factory_settlement_payments"):
        op.drop_table("factory_settlement_payments")
    if _has_table(bind, "factory_orders"):
        if _has_column(bind, "factory_orders", "settlement_payment_id"):
            op.drop_column("factory_orders", "settlement_payment_id")
        if _has_column(bind, "factory_orders", "settlement_month"):
            op.drop_column("factory_orders", "settlement_month")

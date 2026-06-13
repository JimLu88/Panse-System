"""factory_recon_items: 差异处置闭环字段 (Plan L5 — 拆分/归因/确认)。

幂等: 列已存在则跳过。

Revision ID: 0071
Revises: 0070
"""
import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("factory_recon_items")}
    if "parent_item_id" not in cols:
        op.add_column("factory_recon_items", sa.Column("parent_item_id", sa.Integer(), nullable=True))
        op.create_index("ix_factory_recon_items_parent_item_id", "factory_recon_items", ["parent_item_id"])
    if "resolution_kind" not in cols:
        op.add_column("factory_recon_items", sa.Column("resolution_kind", sa.String(length=16), nullable=True))
    if "confirmed_by" not in cols:
        op.add_column("factory_recon_items", sa.Column("confirmed_by", sa.String(length=64), nullable=True))
    if "confirmed_at" not in cols:
        op.add_column("factory_recon_items", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_index("ix_factory_recon_items_parent_item_id", table_name="factory_recon_items")
    for col in ("parent_item_id", "resolution_kind", "confirmed_by", "confirmed_at"):
        op.drop_column("factory_recon_items", col)

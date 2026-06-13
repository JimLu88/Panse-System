"""pricing_sku_costs: bom_synced_at / stale_reason (Plan L7 — 定价配件成本↔BOM 漂移标记)。

幂等: 列已存在则跳过。

Revision ID: 0072
Revises: 0071
"""
import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("pricing_sku_costs")}
    if "bom_synced_at" not in cols:
        op.add_column("pricing_sku_costs", sa.Column("bom_synced_at", sa.DateTime(timezone=True), nullable=True))
    if "stale_reason" not in cols:
        op.add_column("pricing_sku_costs", sa.Column("stale_reason", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("pricing_sku_costs", "stale_reason")
    op.drop_column("pricing_sku_costs", "bom_synced_at")

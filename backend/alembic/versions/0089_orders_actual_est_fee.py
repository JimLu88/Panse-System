"""orders 加 预估/实际 打包+物流费 — 实际账单覆盖预估 (用户 2026-06-21)。

est_packing/est_logistics: 该单按 SKU 定价表(packaging_cost/logistics_cost × qty)的预估分量。
actual_packing/actual_logistics: 精确配到逐单账单的实际分量(打包账单 Σ / 德邦逐单 Σ)。
order_financials.physical_cost: 配到实际时 成本 = 原成本 − 预估 + 实际(只换精确配到的, 未配保持预估)。

Revision ID: 0089
Revises: 0088
"""
import sqlalchemy as sa
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None

_COLS = ("est_packing", "est_logistics", "actual_packing", "actual_logistics")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    for name in _COLS:
        if name not in cols:
            op.add_column("orders", sa.Column(name, sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    for name in _COLS:
        try:
            op.drop_column("orders", name)
        except Exception:  # noqa: BLE001
            pass

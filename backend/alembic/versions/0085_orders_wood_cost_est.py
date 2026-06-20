"""orders 加 wood_cost_est — 工厂账单只含木作时, 补回非木作成本估算。

工厂对账单导入的 actual_cost 只含木作(不含打包/配件/物流/安装/税/平台)。
wood_cost_est = 该单匹配 SKU 的定价表 wood_cost(多产品单=各商品行之和), 供
order_financials.physical_cost 用 actual_cost + (theoretical − wood_est) 补回非木作。

Revision ID: 0085
Revises: 0084
"""
import sqlalchemy as sa
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "wood_cost_est" not in cols:
        op.add_column("orders", sa.Column("wood_cost_est", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    try:
        op.drop_column("orders", "wood_cost_est")
    except Exception:  # noqa: BLE001
        pass

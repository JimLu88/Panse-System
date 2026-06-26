"""orders 加 est_parts (逐单配件「标准估值」; 用户 2026-06-26, 配件 epic P2)。

est_parts = 该单匹配 SKU 的定价表 external_parts_cost × 真实计价件数(与 wood_cost_est 对称)。
用途: ① 大宗材料对账(标准消耗基线: Σ est_parts of 该材料订单, 按发货日期圈窗口)
      ② P3 差异逐单建议值的分摊基数。
不参与 physical_cost(那里配件走 theoretical 反推或 actual_parts 真实计价); est_parts 是
纯「标准/估算」基线列, 由 recompute_and_save / backfill 派生, 默认空。

Revision ID: 0095
Revises: 0094
"""
import sqlalchemy as sa
from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("est_parts", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "est_parts")

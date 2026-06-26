"""orders 加 actual_parts (逐单配件真实成本; 用户 2026-06-26)。

配件(外采)此前全系统只走定价表估算, 无逐单真实录入口。actual_parts = 该单配件真实成本
(来源: ① 配件采购单 related_order_no 汇总[能逐单的料] ② 大宗材料[木皮/洞石板]差异逐单建议值人工回填)。
physical_cost 当 actual_parts 非空 → 改逐项真实计价(木作+物流+安装+打包+真实配件), 跳过占比估算与 85% floor。
默认空, 全系统零影响, 填了才生效。

Revision ID: 0094
Revises: 0093
"""
import sqlalchemy as sa
from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("actual_parts", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "actual_parts")

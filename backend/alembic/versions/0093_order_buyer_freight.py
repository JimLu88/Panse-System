"""orders 加 buyer_freight (买家应付邮费 = 买家额外付的运费, 营收对账基准要加它)。

根因 (2026-06-24 用户实证): 淘宝导出「买家应付邮费」是自定义勾选列, Web-Agent 自动导出没带出来,
导致买家付的运费(代收)在系统里丢失。营收对账比对「支付宝该单收入(含运费) vs 订单净额」时,
缺运费 → 被误报"正差"(分不清是运费还是退款)。补上此列后: base = max(应付,实付) + 邮费 − 退款。
营收/利润口径不含运费(运费≈代收代付, 对利润中性), 故只动对账基准, 不动头条财务。

Revision ID: 0093
Revises: 0092
"""
import sqlalchemy as sa
from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("buyer_freight", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "buyer_freight")

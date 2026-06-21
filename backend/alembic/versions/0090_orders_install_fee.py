"""orders 加 预估/实际 安装费 — 实际覆盖预估扩展到安装 (用户 2026-06-22)。

est_install: 定价表 PricingSku.install_cost × qty(三档兜底同打包/物流)。
actual_install: 订单 install_fee + upstairs_fee(已在订单上, 不用额外匹配)。
physical_cost: 配到实际安装时 成本 = 原成本 − 预估安装 + 实际安装。

Revision ID: 0090
Revises: 0089
"""
import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None

_COLS = ("est_install", "actual_install")


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
